from hashlib import sha1
import re
import datetime

from django.contrib.auth.models import User
from django.contrib.localflavor.us.forms import USPhoneNumberField
from django.core.mail import EmailMessage
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.template.loader import render_to_string

from settings import DEFAULT_FROM_EMAIL
from sandbox_config import SITE_NAME


YES_OR_NO = (
    ('Y', 'YES'),
    ('N', 'NO'),
)

REQUESTER_OR_FUNDER = (
    ('R', 'REQUESTER'),
    ('F', 'FUNDER'),
)


class CFAUser(models.Model):
    """
    A CFA User profile.
    # Create user
    >>> u = User.objects.create_user("alice", "alice@example.com", "1234")
    >>> cfau = u.get_profile()
    >>> cfau.is_requester
    True
    """
    funder_name = models.CharField(max_length=256, default='', blank=True)
    user = models.OneToOneField(User, help_text='You must first create a user '
                                'before adding them to the CFA.')
    user_type = models.CharField(max_length=1, choices=REQUESTER_OR_FUNDER)
    phone = USPhoneNumberField()
    # The e-mail of the contact in OSA
    osa_email = models.EmailField('OSA Contact Email', null=True,
                                  help_text='The email address for contacting '
                                  'OSA when an app is funded.',
                                  blank=True)
    cc_emails = models.ManyToManyField("CCEmail", null=True, blank=True)
    mission_statement = models.TextField(max_length=256, blank=True)

    def __unicode__(self):
        if self.is_funder:
            # some funders might not have funder_names
            return unicode(self.funder_name) or unicode(self.user)
        else:
            return unicode(self.user)

    @property
    def is_funder(self):
        return self.user_type == 'F'

    @property
    def is_requester(self):
        return not self.is_funder

    def requested(self, event):
        """Check if a user requested an event."""
        assert self.is_requester
        return self == event.requester

    def notify_osa(self, event, grants):
        """Notify OSA that an event has been funded."""
        assert self.is_funder
        context = {'funder': self, 'event': event, 'grants': grants}
        subject =\
            render_to_string('app/osa_email_subject.txt', context).strip()
        message = render_to_string('app/osa_email.txt', context)
        recipients = [str(self.osa_email)]
        headers = {'Reply-To': self.user.email}
        email = EmailMessage(subject, message,
                             DEFAULT_FROM_EMAIL, recipients, headers)
        email.send()

    def required_eligibility_question_ids(self):
        question_ids =\
            self.funderconstraint_set.values_list("question_id", flat=True)
        return ','.join(str(id) for id in question_ids)

    class Meta:
        verbose_name = 'CFA Users'
        verbose_name_plural = 'CFA Users'


@receiver(sender=User, signal=post_save)
def create_profile(sender, instance, signal, created, **kwargs):
    """Create a CFAUser whenever a user is created."""
    if created:
        CFAUser.objects.create(user=instance, user_type='R')


class Event(models.Model):
    STATUS = (
        ('S', 'SAVED'),
        ('B', 'SUBMITTED'),
        ('F', 'FUNDED'),
        ('W', 'FOLLOWUP'),
        ('O', 'OVER')
    )
    """An Event object."""
    name = models.CharField(max_length=256)
    date = models.DateField()
    time = models.TimeField()
    location = models.CharField(max_length=256)
    requester = models.ForeignKey(CFAUser, related_name='event_requester')
    contact_name = models.CharField(max_length=256, blank=True)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=15)
    anticipated_attendance = models.IntegerField()
    advisor_email = models.EmailField(blank=True)
    advisor_phone = models.CharField(max_length=15, blank=True)
    organizations = models.CharField(max_length=256)
    applied_funders =\
        models.ManyToManyField(CFAUser,
                               related_name='event_applied_funders')
    funding_already_received = \
        models.DecimalField(max_digits=17, decimal_places=2, default=0)
    status = models.CharField(max_length=1, choices=STATUS)
    created_at = models.DateTimeField(default=datetime.datetime.now)
    updated_at = models.DateTimeField(default=datetime.datetime.now)

    def save(self, *args, **kwargs):
        self.updated_at = datetime.datetime.now()
        return super(Event, self).save(*args, **kwargs)

    @property
    def over(self):
        return self.status == 'O'

    @property
    def saved(self):
        return self.status == 'S'

    @property
    def followup_needed(self):
        return self.status == 'W'

    @property
    def submitted(self):
        return self.status == 'B'

    @property
    def locked(self):
        return self.funded or self.over
        # return self.submitted or self.funded or self.over

    @property
    def total_funds_already_received(self):
        """
        The total amount of money already received
        (before grants) for an event.
        """
        return self.funding_already_received +\
            sum(item.funding_already_received for item in self.item_set.all())

    @property
    def amounts(self):
        """Get a dictionary containing the amount each funder has granted."""
        amounts = dict((funder, None) for funder in self.applied_funders.all())
        for item in self.item_set.all():
            for grant in item.grant_set.all():
                if amounts[grant.funder] is None:
                    amounts[grant.funder] = grant.amount
                else:
                    amounts[grant.funder] += grant.amount
        return amounts

    @property
    def total_funds_granted(self):
        """The total amount of money received via grants."""
        sum = 0
        for val in self.amounts.values():
            sum = sum + val if val is not None else sum
        return sum

    @property
    def funded(self):
        """Whether or not an event has been funded."""
        return self.status == 'F'

    @property
    def total_funds_received(self):
        """The total amount of money received (grants + pre grant)."""
        return self.total_funds_already_received + self.total_funds_granted

    @property
    def total_expense(self):
        """The total amount of money requested for an event."""
        return sum(item.total for item in self.item_set.all() if not item.revenue)

    @property
    def total_additional_funds(self):
        """The tatal amount of non-CFA funding and admission fees."""
        return sum(item.total for item in self.item_set.all() if item.revenue)

    @property
    def total_remaining(self):
        return (self.total_expense - self.total_funds_received -
                self.total_additional_funds)

    @property
    def date_passed(self):
        return datetime.date.today() > self.date + datetime.timedelta(days=14)

    @property
    def comments(self):
        return self.comment_set.order_by('created')

    def save_from_form(self, POST):
        """Save an event from form data."""
        # save items
        names = POST.getlist('item_name')
        quantities = POST.getlist('item_quantity')
        prices_per_unit = POST.getlist('item_price_per_unit')
        funding_already_received =\
            POST.getlist('item_funding_already_received')
        categories = POST.getlist('item_category')
        revenues = POST.getlist('item_revenue')

        for item in self.item_set.all():
            if not Grant.objects.filter(item = item):
                item.delete()

        zipped_items = zip(names, quantities, prices_per_unit,
                           funding_already_received, categories, revenues)
        for name, quantity, price, funding, cat, rev in zipped_items:
            if Item.objects.filter(event = self, name = name):
                continue
            funding = funding or 0
            # set correct category letter
            for tup in CATEGORIES:
                if tup[1] == cat:
                    cat = tup[0]

            if str(name):
                self.item_set.create(name=name,
                                     quantity=quantity,
                                     price_per_unit=price,
                                     funding_already_received=funding,
                                     category=cat,
                                     revenue=int(rev))

        # save questions

        # delete existing answers
        self.commonfollowupanswer_set.all().delete()
        self.followupanswer_set.all().delete()
        self.eligibilityanswer_set.all().delete()
        self.commonfreeresponseanswer_set.all().delete()
        self.freeresponseanswer_set.all().delete()

        # clear existing funders to re-add new ones
        self.applied_funders.clear()

        # create new answers and save funders
        # unchecked checkboxes will have neither answers nor funders
        # associated with them
        for k, v in POST.items():
            if k.startswith('eligibility'):
                q_id = re.search("[0-9]+", k).group(0)
                question = EligibilityQuestion.objects.get(id=q_id)
                self.eligibilityanswer_set.create(question=question,
                                                  event=self, answer='Y')
            elif k.startswith('commonfollowup'):
                q_id = re.search("[0-9]+", k).group(0)
                question = CommonFollowupQuestion.objects.get(id=q_id)
                self.commonfollowupanswer_set.create(question=question,
                                                     event=self, answer=v)
            elif k.startswith('followup'):
                q_id = re.search("[0-9]+", k).group(0)
                question = FollowupQuestion.objects.get(id=q_id)
                self.followupanswer_set.create(question=question,
                                               event=self, answer=v)
            elif k.startswith('commonfreeresponse'):
                q_id = re.search("[0-9]+", k).group(0)
                question = CommonFreeResponseQuestion.objects.get(id=q_id)
                self.commonfreeresponseanswer_set.create(question=question,
                                                         event=self, answer=v)
            elif k.startswith('freeresponse'):
                q_id = re.search("[0-9]+", k).group(0)
                question = FreeResponseQuestion.objects.get(id=q_id)
                self.freeresponseanswer_set.create(question=question,
                                                   event=self, answer=v)
            elif k.startswith('funder'):
                funder_id = re.search("[0-9]+", k).group(0)
                funder = CFAUser.objects.get(id=funder_id)
                self.applied_funders.add(funder)

    def notify_funders(self, new=False):
        """Notify all the funders of an event that they have been applied to"""
        context = {'requester': self.requester, 'event': self}

        template =\
            'app/application_email' if new else 'app/application_changed'

        subject =\
            render_to_string('%s_subject.txt' % template, context).strip()
        message = render_to_string('%s.txt' % template, context)

        for funder in self.applied_funders.all():
            self.notify_funder(subject, message, funder)

    def notify_funder(self, subject, message, funder):
        """Notify a funder that the requester has applied to them."""
        assert funder.is_funder
        email = EmailMessage(subject=subject,
                             body=message,
                             from_email=DEFAULT_FROM_EMAIL,
                             to=[funder.user.email],
                             cc=funder.cc_emails.values_list('email',
                                                             flat=True))
        email.send()

    def notify_requester(self, grants):
        """Notify a requester that an event has been funded."""
        context = {'event': self, 'grants': grants}
        subject = render_to_string('app/grant_email_subject.txt',
                                   context).strip()
        message = render_to_string('app/grant_email.txt', context)
        self.requester.user.email_user(subject, message)

    def notify_requester_for_followups(self):
        """
        Notify a requester that his event is over
        and he needs to answer followup questions
        """
        context = {'event': self, 'SITE_NAME': SITE_NAME}
        subject = render_to_string('app/over_event_email_subject.txt',
                                   context).strip()
        html_content = render_to_string('app/over_event_email.txt', context)
        email = EmailMessage(subject=subject,
                             body=html_content,
                             from_email=DEFAULT_FROM_EMAIL,
                             to=[self.requester.user.email],
                             cc=self.requester.cc_emails
                                    .values_list('email', flat=True))
        email.content_subtype = "html"  # main content is not text/html
        email.send()

    @property
    def secret_key(self):
        """Unique key that can be shared so that anyone can view the event."""
        """To use the key append ?key=<key>"""
        return sha1("".join([self.name, str(self.date), str(self.requester)])
                    .encode("utf-8")).hexdigest()

    @models.permalink
    def get_absolute_url(self):
        return ('app.views.event_show', [str(self.id)])

    def __unicode__(self):
        return "%s: %s, %s" % (unicode(self.requester),
                               self.name,
                               self.date.isoformat())

    class Meta:
        unique_together = ("name", "date", "requester")


class Comment(models.Model):
    """A comment, made by a funder, on an event application."""
    funder = models.ForeignKey(CFAUser)
    event = models.ForeignKey(Event)
    comment = models.TextField()
    created = models.DateTimeField(auto_now_add=True)

    def __unicode__(self):
        return self.comment


@receiver(sender=Comment, signal=post_save)
def notify_requester(sender, instance, signal, created, **kwargs):
    if created:
        subject = 'You have a new comment on your funding application'
        message = ('%s left a comment on your application for an event on %s.'
                   '%s said "%s" See the full application at %s.') %\
                  (instance.funder, instance.event.date, instance.funder,
                   instance.comment, 'link here')
        sender = "no-reply@pennapps.com"
        recipients = [instance.event.contact_email]
        headers = {'Reply-To': instance.funder.user.email}
        email = EmailMessage(subject, message, sender, recipients, headers)
        email.send()


class Question(models.Model):
    question = models.TextField()

    def __unicode__(self):
        return self.question

    class Meta:
        abstract = True


class Answer(models.Model):
    event = models.ForeignKey(Event)

    def __unicode__(self):
        return "%s %s" % (unicode(self.question), self.answer)

    class Meta:
        abstract = True


class EligibilityQuestion(Question):
    def recs(self, answer):
        return self.funderconstraint_set.filter(
            answer=answer).values_list("funder_id", flat=True)

    def required_funders(self):
        return map(lambda fc: fc.funder, self.funderconstraint_set.all())

    def required_funder_ids(self):
        funder_ids = self.funderconstraint_set.values_list("funder_id", flat=True)
        return ','.join(str(id) for id in funder_ids)

    @property
    def recs_yes(self):
        """Return the funder ids that want Yes on the question"""
        funder_ids = self.recs('Y')
        return ','.join(str(a) for a in funder_ids)

    @property
    def recs_no(self):
        """Return the funder ids that want No on the question"""
        funder_ids = self.recs('N')
        return ','.join(str(a) for a in funder_ids)


class EligibilityAnswer(Answer):
    question = models.ForeignKey(EligibilityQuestion)
    answer = models.CharField(max_length=1, choices=YES_OR_NO)

    class Meta:
        unique_together = ("question", "event", "answer")


class CommonFollowupQuestion(Question):
    """A followup question common to all funders."""
    pass


class CommonFollowupAnswer(Answer):
    question = models.ForeignKey(CommonFollowupQuestion)
    answer = models.TextField()


class FollowupQuestion(Question):
    """A followup question specific to a funder."""
    funder = models.ForeignKey(CFAUser)


class FollowupAnswer(Answer):
    question = models.ForeignKey(FollowupQuestion)
    answer = models.TextField()


class CommonFreeResponseQuestion(Question):
    """A free response question common to all funders."""
    pass


class CommonFreeResponseAnswer(Answer):
    """An answer to a common free response question."""
    question = models.ForeignKey(CommonFreeResponseQuestion)
    answer = models.TextField()


class FreeResponseQuestion(Question):
    """A unique free response question specified by a single funder."""
    funder = models.ForeignKey(CFAUser)


class FreeResponseAnswer(Answer):
    question = models.ForeignKey(FreeResponseQuestion)
    answer = models.TextField()

CATEGORIES = (
    ('H', 'Honoraria/Services'),
    ('E', 'Equipment/Supplies'),
    ('F', 'Food/Drinks'),
    ('S', 'Facilities/Security'),
    ('T', 'Travel/Conference'),
    ('P', 'Photocopies/Printing/Publicity'),
    ('O', 'Other'),
)


class Item(models.Model):
    """An item for an event."""
    event = models.ForeignKey(Event)
    name = models.CharField(max_length=256)
    # number of items
    quantity = models.IntegerField()
    # cost per item
    price_per_unit = models.DecimalField(max_digits=17, decimal_places=2)
    # funding already received before applications
    funding_already_received = models.DecimalField(max_digits=17,
                                                   decimal_places=2)
    category = models.CharField(max_length=1, choices=CATEGORIES)
    revenue = models.BooleanField()

    @property
    def total(self):
        return self.price_per_unit * self.quantity

    @property
    def total_grants(self):
        return sum([grant.amount for grant in self.grant_set.all()])

    @property
    def total_received(self):
        return self.funding_already_received + self.total_grants

    def __unicode__(self):
        return self.name


class Grant(models.Model):
    funder = models.ForeignKey(CFAUser)
    item = models.ForeignKey(Item)
    amount = models.DecimalField(max_digits=17, decimal_places=2, null=True)

    def __unicode__(self):
        return "%s, %s, %d" % (unicode(self.item),
                               unicode(self.funder),
                               self.amount)

    class Meta:
        unique_together = ("funder", "item")


class FunderConstraint(models.Model):
    """
    Questions which funders require (yes/no/don't care) answers to
    by the requesters in order to be eligible to recieve money
    """
    funder = models.ForeignKey(CFAUser)
    question = models.ForeignKey(EligibilityQuestion)
    answer = models.CharField(max_length=1, choices=YES_OR_NO)

    def __unicode__(self):
        return "%s, %s %s" % (unicode(self.funder),
                              unicode(self.question),
                              self.answer)

    class Meta:
        unique_together = ("funder", "question")


class CCEmail(models.Model):
    """Emails which can be CCd for every funder"""
    email = models.EmailField()

    def __unicode__(self):
        return self.email
