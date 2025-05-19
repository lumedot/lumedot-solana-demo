import os
import sys
import threading
import asyncio
from flask import Flask, request, jsonify
from flask_cors import CORS
from ariadne import graphql_sync, load_schema_from_path, make_executable_schema

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from resolvers.purchase_subscriptions.mutation import purchase_subscriptions_mutation
from resolvers.purchase_subscriptions.query    import purchase_subscriptions_query
from resolvers.purchase_titles.mutation        import purchase_titles_mutation
from resolvers.purchase_titles.query           import purchase_titles_query
from utils.helius_webhook import handle_webhook
from utils.helius_listener import HeliusListener

def _run_listener():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    listener = HeliusListener()
    loop.run_until_complete(listener.start())

threading.Thread(target=_run_listener, daemon=True).start()

app = Flask(__name__)
CORS(app)

GRAPHQL_PATH = os.path.join(PROJECT_ROOT, "graphql", "schema.graphql")
type_defs = load_schema_from_path(GRAPHQL_PATH)

schema = make_executable_schema(
    type_defs,
    purchase_subscriptions_mutation,
    purchase_subscriptions_query,
    purchase_titles_mutation,
    purchase_titles_query,
)

PLAYGROUND_HTML = """<!DOCTYPE html><html><head>
  <meta charset="utf-8"/>
  <title>GraphQL Playground</title>
  <link rel="stylesheet" href="//cdn.jsdelivr.net/npm/graphql-playground-react/build/static/css/index.css"/>
  <script src="//cdn.jsdelivr.net/npm/graphql-playground-react/build/static/js/middleware.js"></script>
</head><body>
<div id="root"></div><script>
window.addEventListener('load', function() {
  GraphQLPlayground.init(document.getElementById('root'), { endpoint: '/graphql' });
});
</script></body></html>"""

@app.route("/graphql", methods=["GET", "POST"])
def graphql_route():
    if request.method == "GET":
        return PLAYGROUND_HTML, 200
    ok, result = graphql_sync(schema, request.get_json(), context_value=request)
    return jsonify(result), (200 if ok else 400)


@app.route("/helius/webhook", methods=["POST"])
def helius_webhook():
    return handle_webhook(request.get_data(), request.headers)


@app.route("/")
def index():
    return "lumedot-solana-demo", 200

if __name__ == '__main__':
    app.run(debug=True)
