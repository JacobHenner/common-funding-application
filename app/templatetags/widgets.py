from app.templatetags.helpers import funder_item_data
from app.models import CFAUser

from django import template
from django.template import RequestContext
from django.template.loader import render_to_string

from templatetag_sugar.parser import Variable, Optional, Constant, Name
from templatetag_sugar.register import tag

from collections import namedtuple

register = template.Library()

@tag(register, [Variable(), Variable()])
def fundingbar(context, totalAmount, fundDict):
  # takes a dictionary {funder:amount}  
  
  FundItem = namedtuple('FundItem', ['funder', 'amount', 'percent'])
  fundItems = []
  currentAmount = 0;
  for k in fundDict:
    fundItems.append(FundItem(k,
                              amount=fundDict[k],
                              percent=fundDict[k]*100/totalAmount))
    currentAmount += fundDict[k];
    
  new_context = {'fundItems': fundItems,
                 'currentAmount': currentAmount,
                 'totalAmount': totalAmount}
  
  return render_to_string('app/templatetags/fundingbar.html', new_context)

@tag(register, [Variable()])
def itemlist_requester(context, items):
  # takes a dictionary of items
  new_context = {'items':items}
  return render_to_string('app/templatetags/itemlist-requester.html', new_context)

@tag(register, [Variable(), Variable()])
def itemlist_funder(context, item_list, funder_id):
  funders = CFAUser.objects.filter(user_type='F')
  items_data = []
  title_row = ['Name', 'Quantity', 'Price Per Unit', 'Total Amount']
  for funder in funders:
    title_row.append(funder.user.username)
  for item in item_list:
    items_data.append(funder_item_data(context, item, funders))
  new_context = {'titles': title_row,
                'current_funder': funder_id,
                'items_data': items_data}
  return render_to_string('app/templatetags/itemlist-funder.html', new_context)
