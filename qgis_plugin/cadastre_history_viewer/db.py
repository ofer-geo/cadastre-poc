import psycopg2
from qgis.core import QgsProject, QgsDataSourceUri

_D = {
    'host': 'localhost', 'port': '5432',
    'dbname': 'cadastre_poc', 'user': 'postgres', 'password': 'postgres'
}

def get_connection():
    p = dict(_D)
    for layer in QgsProject.instance().mapLayers().values():
        u = QgsDataSourceUri(layer.dataProvider().dataSourceUri())
        if u.host() and u.database():
            if u.host():     p['host']     = u.host()
            if u.port():     p['port']     = u.port()
            if u.database(): p['dbname']   = u.database()
            if u.username(): p['user']     = u.username()
            if u.password(): p['password'] = u.password()
            break
    return psycopg2.connect(
        host=p['host'], port=int(p['port']),
        dbname=p['dbname'], user=p['user'], password=p['password']
    )
