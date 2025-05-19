from ariadne import MutationType
from .query import purchase_titles_query
from utils.price_check import create_title_session

purchase_titles_mutation = MutationType()

@purchase_titles_mutation.field("createTitlePurchaseSession")
def resolve_create_title_purchase_session(_, info, userId, bookId, purchaseType):
    return create_title_session(userId, bookId, purchaseType)
