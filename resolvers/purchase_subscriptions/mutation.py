from ariadne import MutationType
from .query import purchase_subscriptions_query
from utils.price_check import create_sub_session

purchase_subscriptions_mutation = MutationType()

@purchase_subscriptions_mutation.field("createSubscriptionPurchaseSession")
def resolve_create_subscription_purchase_session(_, info, userId, purchaseType):
    return create_sub_session(userId, purchaseType)
