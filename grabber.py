import argparse
import requests
import os
import glob, shutil
import datetime
import json
import time
import gzip
import pickle

from apscheduler.schedulers.background import BackgroundScheduler
import trio
from dotenv import load_dotenv

import Database as db
import GTFS2GeoJSON

from config import config


def get_path_list():
    path_list = []
    url = "http://bustime.mta.info/api/where/routes-for-agency/MTA%20NYCT.json?key=" + os.getenv("API_KEY")

    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 503: # response is bad, so go to exception and load the pickle
            raise Exception(503, "503 error code fetching route definitions. OneBusAway API probably overloaded.")
        else: # response is good, so save it to pickle and proceed
            with open((filepath() + 'routes-for-agency.pickle'), "wb") as pickle_file:
                pickle.dump(response,pickle_file)
    except Exception as e: # response is bad, so load the last good pickle
        with open((filepath() + 'routes-for-agency.pickle'), "rb") as pickle_file:
            response = pickle.load(pickle_file)
        print("Route URLs loaded from pickle cache.")
    finally:
        routes = response.json()
        print('Found {} routes.'.format(len(routes['data']['list'])))

    for route in routes['data']['list']:
        path_list.append({route['id']:"/api/siri/vehicle-monitoring.json?key={}&VehicleMonitoringDetailLevel=calls&LineRef={}".format(os.getenv("API_KEY"), route['id'])})

    return path_list


def filepath():
    path = ("data/")
    check = os.path.isdir(path)
    if not check:
        os.makedirs(path)
        print("created folder : ", path)
    else:
        pass
    return path


def get_db_args():

    if args.localhost is True: #n.b. this ignores what's in config/development.py
        dbhost = 'localhost'
    elif os.environ['PYTHON_ENV'] == "development":
        dbhost = 'localhost'
    else:
        dbhost = config.config['dbhost']

    return (config.config['dbuser'],
            config.config['dbpassword'],
            dbhost,
            config.config['dbname']
            )

def dump_to_file(feeds):
    timestamp = datetime.datetime.now()
    timestamp_pretty = timestamp.strftime("%Y-%m-%dT_%H:%M:%S.%f")
    for route_bundle in feeds:
        for route_id,route_report in route_bundle.items():
            dumpfile=(filepath() + timestamp_pretty + '_' + route_id.split()[1] +'.gz')
            with gzip.open(dumpfile, 'wt', encoding="ascii") as zipfile:
                try:
                    json.dump(route_report.json(), zipfile)
                except:
                    pass # if error, dont write and return
    return timestamp


# https://programmersought.com/article/77402568604/
def rotate_files(): #future this is really hacky, rewrite
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days = 1) # e.g. 2020-10-04
    # print ('today is {}, yesterday was {}'.format(today,yesterday))
    datapath = './data/'
    outfile = '{}daily-{}.gz'.format(datapath, yesterday)
    # print ('bundling minute grabs from {} into {}'.format(yesterday,outfile))
    all_gz_files = glob.glob("{}*.gz".format(datapath))
    yesterday_gz_files = []
    for file in all_gz_files:
        if file[7:17] == str(yesterday): # this should parse the path using os.path.join?
            yesterday_gz_files.append(file)
    # print ('adding {} files'.format(len(yesterday_gz_files)))
    with open(outfile, 'wb') as wfp:
        for fn in yesterday_gz_files:
            with open(fn, 'rb') as rfp:
                shutil.copyfileobj(rfp, wfp)
    for file in yesterday_gz_files:
        os.remove(file)


def dump_to_db(timestamp, feeds):
    db_url=db.get_db_url(*get_db_args())
    db.create_table(db_url)
    session = db.get_session(*get_db_args())
    print('Dumping to {}'.format(db_url))
    num_buses = 0
    for route_bundle in feeds:
        for route_id,route_report in route_bundle.items():
            buses = db.parse_buses(timestamp, route_id, route_report.json(), db_url)
            for bus in buses:
                session.add(bus)
                num_buses = num_buses + 1
        session.commit()
    #todo execute the same query here as in /livemap view, and dump the results to api-www.results_to_FeatureCollection >>> /static/lastknownpositions.geojson
    return num_buses


def async_grab_and_store():

    start = time.time()
    path_list = get_path_list()
    feeds = []

    async def grabber(s,a_path,route_id):
        try:
            r = await s.get(path=a_path) # bug need to trap connection errors here
        except ValueError as e :
            print ('{} from DNS issues'.format(e))
        feeds.append({route_id:r})

    async def main(path_list):
        from asks.sessions import Session

        if args.localhost is True:
            s = Session('http://bustime.mta.info', connections=5) #todo load this from config!
        else:
            s = Session('http://bustime.mta.info', connections=config.config['http_connections'])
        async with trio.open_nursery() as n:
            for path_bundle in path_list:
                for route_id,path in path_bundle.items():
                    n.start_soon(grabber, s, path, route_id )

    trio.run(main, path_list)

    timestamp = dump_to_file(feeds)
    num_buses = dump_to_db(timestamp, feeds)
    end = time.time()
    print('Fetched {} buses on {} routes in {:2f} seconds to gzipped archive and mysql database.\n'.format(num_buses,len(feeds),(end - start)))

    return



if __name__ == "__main__":

    print('NYC MTA BusTime API Scraper v1.0. October 2020. Anthony Townsend <atownsend@cornell.edu>')
    print('mode: {}'.format(os.environ['PYTHON_ENV']))

    parser = argparse.ArgumentParser(description='NYCbuswatcher grabber, fetches and stores current position for buses')
    parser.add_argument('-l', action="store_true", dest="localhost", help="force localhost for production mode")
    args = parser.parse_args()

    load_dotenv()

    # PRODUCTION = start main loop
    if os.environ['PYTHON_ENV'] == "production":
        interval = 60
        print('Scanning on {}-second interval.'.format(interval))
        # scheduler = BackgroundScheduler({
        #                                     'apscheduler.jobstores.default':
        #                                         {
        #                                             'type': 'sqlalchemy',
        #                                             'url': 'sqlite:///jobs.sqlite'
        #                                         },
        #                                     'apscheduler.job_defaults.coalesce': 'false',
        #                                     'apscheduler.job_defaults.max_instances': '3',
        #                                     'apscheduler.timezone': 'UTC',
        #                                 })
        scheduler = BackgroundScheduler()
        scheduler.add_job(async_grab_and_store, 'interval', seconds=interval, max_instances=1, misfire_grace_time=15)
        scheduler.add_job(GTFS2GeoJSON.update_route_map, 'cron', hour='2') #run at 2am daily
        scheduler.add_job(rotate_files,'cron', hour='1') #run at 1 am daily
        scheduler.start()
        try:
            while True:
                time.sleep(2)
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown()

    # DEVELOPMENT = run once and quit
    elif os.environ['PYTHON_ENV'] == "development":
        async_grab_and_store()



