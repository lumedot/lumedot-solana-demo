from ariadne import QueryType

purchase_subscriptions_query = QueryType()

@purchase_subscriptions_query.field("_")
def placeholder(_, info):
    return True
