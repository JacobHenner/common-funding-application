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
  for k in fundDict:
    fundItems.append(
      FundItem(
        k,
        amount = fundDict[k],
        percent = fundDict[k]*100.0/totalAmount
        ))
    
  new_context = {
    'fundItems' : fundItems
  }
  
  return render_to_string('templatetags/fundingbar.html', new_context)