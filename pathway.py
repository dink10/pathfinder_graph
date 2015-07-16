
import httplib, urllib
import json
from py2neo import Graph
from flask import Flask, request, Response, jsonify
from flask_sockets import Sockets
import threading
import os
from caleydo_server.config import view as configview

app = Flask(__name__)
sockets = Sockets(app)

class Config(object):
  def __init__(self, config_file):
    print config_file
    with open(config_file, 'r') as f:
      r = json.load(f)
    self.raw = r
    sett = r.get('settings',dict())
    c = configview('pathway')
    self.port = sett.get('port',c.port)
    self.host = sett.get('host', c.host)
    self.url = sett.get('url','http://'+self.host+':'+str(self.port))

    self.node_label = sett.get('node_label','_Network_Node')
    self.set_label = sett.get('set_label','_Set_Node')

    self.directions = sett.get('directions', dict(Edge='out',ConsistsOfEdge='both')) #both ConsistsOf for the reversal
    #by default inline ConsistsOfEdges
    self.inline = sett.get('inline', dict(inline='ConsistsOfEdge',undirectional=False,flag='_isSetEdge',aggregate='pathways',toaggregate='id',type='Edge'))

configs = dict()
config_dir = configview('caleydo').clientDir+'/external/caleydo_pathfinder/uc/'
for f in os.listdir(config_dir):
  configs[f.replace('.json','')] = Config(os.path.join(config_dir,f))

config = None

@app.before_request
def resolve_usecase():
  global config
  uc = request.cookies.get('uc','dblp')
  config = configs[uc]

def resolve_db():
  graph = Graph(config.url + "/db/data/")
  return graph

@app.route('/config.json')
def get_config():
  return jsonify(config.raw)

def preform_search(s, limit=20, label = None, prop = 'name'):
  if label is None:
    label = config.node_label
  """ performs a search for a given search query
  :param s:
  :param limit: maximal number of results
  :return:
  """
  if len(s) < 2:  # too short search query
    return []

  import re
  # convert to reqex expression
  s = '.*' + re.escape(s.lower()).replace('\\','\\\\') + '.*'

  graph = resolve_db()

  query = 'MATCH (n:{0}) WHERE n.{1} =~ "(?i){2}" RETURN id(n) as id, n.{1} as name, n.id as nid ORDER BY n.{1} LIMIT {3}'.format(label, prop, s, limit)

  print query

  records = graph.cypher.execute(query)

  def convert(result):
    return dict(value=result.id, label=result.name, id=result.nid)

  return [convert(r) for r in records]


@app.route("/search")
def find_node():
  s = request.args.get('q', '')
  limit = request.args.get('limit', 20)
  label = request.args.get('label', config.node_label)
  prop = request.args.get('prop','name')

  results = preform_search(s, limit, label, prop)

  return jsonify(q=s, linit=limit, label=label, prop=prop, results=results)

def parse_incremental_json(text, on_chunk):
  """
  an incremental json parser, assumes a data stream like: [{...},{...},...]
  :param text: text to parse
  :param on_chunk: callback to call when a chunk was found
  :return: the not yet parsed text
  """
  act = 0
  open_braces = 0
  l = len(text)

  if text[act] == '[' or text[act] == ',': #skip initial:
    act = 1

  start = act

  while act < l:
    c = text[act]
    if c == '{': #starting object
      open_braces += 1
    elif c == '}': #end object
      open_braces -= 1
      if open_braces == 0: #at the root
        on_chunk(json.loads(text[start:act+1]))
        start = act + 1
        act += 1
        if act < l and text[act] == ',': #skip separator
          start += 1
          act += 1
    act += 1
  if start == 0:
    return text
  return text[start:]

class Query(object):
  def __init__(self, q, socket_ns):
    self.q = q
    self.conn = httplib.HTTPConnection(config.host, config.port)
    self.socket_ns = socket_ns
    self.shutdown = threading.Event()
    self.t = threading.Thread(target=self.stream)
    self.paths = []

  def abort(self):
    if self.shutdown.isSet():
      return
    self.conn.close()
    self.shutdown.set()

  def send_path(self, path):
    if self.shutdown.isSet():
      return
    self.paths.append(path)
    print 'sending path ',len(self.paths)
    self.send_impl('query_path',dict(query=self.q,path=path,i=len(self.paths)))

  def send_impl(self, t, msg):
    #print 'send'+t+str(msg)
    d = json.dumps(dict(type=t,data=msg))
    self.socket_ns.send(d)

  def send_start(self):
    self.send_impl('query_start',dict(query=self.q))

  def send_done(self):
    print 'sending done ',len(self.paths)
    self.send_impl('query_done',dict(query=self.q)) #,paths=self.paths))

  def stream(self):
    response = self.conn.getresponse()
    if self.shutdown.isSet():
      print 'aborted early'
      return
    content_length = int(response.getheader('Content-Length', '0'))
    print 'waiting for response: '+str(content_length)
    # Read data until we've read Content-Length bytes or the socket is closed
    l = 0
    data = ''
    while not self.shutdown.isSet() and (l < content_length or content_length == 0):
      s = response.read(4)  #read at most 32 byte
      if not s or self.shutdown.isSet():
        break
      data += s
      l += len(s)
      data = parse_incremental_json(data,self.send_path)

    if self.shutdown.isSet():
      print 'aborted'
      return

    parse_incremental_json(data,self.send_path)
    # print response.status, response.reason
    #data = response.read()
    self.send_done()

    self.conn.close()
    self.shutdown.set()
    print 'end'

  def run(self):
    headers = {
      'Content-type': 'application/json',
      'Accept': 'application/json'
      }
    args = { k : json.dumps(v) if type(v) is dict else v for k,v in self.q.iteritems()}
    print args
    args = urllib.urlencode(args)
    url = '/caleydo/kShortestPaths/?{0}'.format(args)
    print url
    body = ''
    self.conn.request('GET', url, body, headers)
    self.send_start()
    self.t.start()

current_query = None

@sockets.route('/query')
def websocket_query(ws):
  global current_query
  while True:
    msg = ws.receive()
    print msg
    data = json.loads(msg)
    t = data['type']
    payload = data['data']
    if t == 'query':
      if current_query is not None:
        current_query.abort()
      current_query = Query(to_query(payload), ws)
      current_query.run()

def to_query(msg):
  k = msg.get('k',1)
  max_depth = msg.get('maxDepth', 10)
  just_network = msg.get('just_network_edges', False)
  q = msg['query']
  print q

  args = {
    'k': k,
    'maxDepth': max_depth,
  }

  constraint = {'context': 'node', '$contains' : config.node_label}

  #TODO generate from config
  directions = config.directions
  inline = config.inline

  if q is not None:
    constraint = {'$and' : [constraint, q] }

  args['constraints'] = dict(c=constraint,dir=directions,inline=inline,acyclic=True)
  if just_network:
    directions = dict(directions)
    del directions[inline['inline']]
    c = args['constraints']
    del c['inline']

  return args

@app.route("/summary")
def get_graph_summary():

  graph = resolve_db()

  def compute():
    query = 'MATCH (n:{0}) RETURN COUNT(n) AS nodes'.format(config.node_label)
    records = graph.cypher.execute(query)
    num_nodes = records[0].nodes

    query = 'MATCH (n1:{0})-[e]->(n2:{0}) RETURN COUNT(e) AS edges'.format(config.node_label)
    records = graph.cypher.execute(query)
    num_edges = records[0].edges

    query = 'MATCH (n:{0}) RETURN COUNT(n) AS sets'.format(config.set_label)
    records = graph.cypher.execute(query)
    num_sets = records[0].sets

    yield json.dumps(dict(Nodes=num_nodes,Edges=num_edges,Sets=num_sets))

  return Response(compute(), mimetype='application/json')


def create_get_sets_query(sets):
  # convert to query form
  set_queries = ['"{0}"'.format(s) for s in sets]

  #create the query
  return 'MATCH (n:{1}) WHERE n.id in [{0}] RETURN n, n.id as id, id(n) as uid'.format(', '.join(set_queries), config.set_label)


@app.route("/setinfo")
def get_set_info():
  sets = request.args.getlist('sets[]')
  print sets
  if len(sets) == 0:
    return jsonify()

  graph = resolve_db()

  def compute():
    query = create_get_sets_query(sets)
    records = graph.cypher.execute(query)

    response = dict()

    for record in records:
      node = record.n

      response[record.id] = {
        'id': record.uid,
        'labels': [str(l) for l in node.labels],
        'properties': node.properties
      }
    print 'sent setinfo for ',sets
    yield json.dumps(response)

  return Response(compute(), mimetype='application/json')

def create(*args, **kwargs):
  """
   entry point of this plugin
  """
  app.debug = True
  return app


if __name__ == '__main__':
  app.debug = True
  app.run(host='0.0.0.0')
