import json, os
import argparse
from import_utils import GraphImporter

parser = argparse.ArgumentParser(description='Marclab importer')
parser.add_argument('--db', default='http://localhost:7477/db/data/')
# parser.add_argument('--db', default='http://192.168.50.52:7477/db/data/')
parser.add_argument('--data_file', '-d', default='./data/marclab_476_4hops.json',
                    help='data directory')
# parser.add_argument('--data_dir', '-d', default='/vagrant_data/kegg/',
#                    help='data directory')
parser.add_argument('--clear', action='store_true', help='clear the graph')
parser.add_argument('--commitEvery', type=int, default=100, help='commit every x steps')
args = parser.parse_args()

importer = GraphImporter(args.db, args.commitEvery)
if args.clear or True:
  importer.delete_all()

with open(args.data_file) as f:
  doc = json.load(f)

  nodes = doc["nodes"]

  for node in nodes:
    if node["Label"] is not None:
      importer.add_node(['_Network_Node', 'Structure'], str(node["StructureID"]),
                      {'name': str(node["StructureID"]), 'labels': [str(node["Label"])]})
      importer.add_node(['_Set_Node', 'Label'], str(node["Label"]),
                        {'name': str(node["Label"])})
      importer.add_edge('ConsistsOf', str(node["Label"]), str(node["StructureID"]), {}, 'Label')
    else:
      importer.add_node(['_Network_Node', 'Structure'], str(node["StructureID"]),
                      {'name': str(node["StructureID"]), 'labels': []})

  edges = doc["edges"]

  for edge in edges:
    importer.add_edge('Edge', str(edge["SourceStructureID"]), str(edge["TargetStructureID"]), {'_isNetworkEdge': True,
                                                                                     'Type': edge["Type"], 'Label': edge["Label"]})


  # importer.add_node(['_Network_Node', 'Compound'], compound_id,
  #                   {'name': cpdName, 'idType': 'KEGG_COMPOUND',
  #                    'url': 'http://www.kegg.jp/dbget-bin/www_bget?cpd:' + compound_id})
  # importer.add_edge('Edge', gene_id, substrate_id, {'_isNetworkEdge': True})