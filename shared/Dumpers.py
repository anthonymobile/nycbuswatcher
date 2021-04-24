import requests
import os
import glob, shutil
import datetime
import json
import gzip
import pickle

import geojson


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
        now=datetime.datetime.now()
        print('Found {} routes at {}.'.format(len(routes['data']['list']),now.strftime("%Y-%m-%d %H:%M:%S")))

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



def to_file(feeds):
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


# todo get more aggressive, rotate hourly?
# todo check if actually deleting the old individual response files on last line
# https://programmersought.com/article/77402568604/
def rotate_files():
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
        os.remove(file) #bug



def to_lastknownpositions(feeds): #future please refactor me
    f_list=[]
    for route_bundle in feeds:
        for route_id,route_report in route_bundle.items():
            route_report = route_report.json()
            try:
                for b in route_report['Siri']['ServiceDelivery']['VehicleMonitoringDelivery'][0]['VehicleActivity']:
                    p = geojson.Point((b['MonitoredVehicleJourney']['VehicleLocation']['Longitude'],
                                       b['MonitoredVehicleJourney']['VehicleLocation']['Latitude']))

                    try:
                        occupancy={'occupancy':b['MonitoredVehicleJourney']['Occupancy']}
                    except KeyError:
                        occupancy = {'occupancy': 'empty'}

                    try:
                        passengers={'passengers': int(b['MonitoredVehicleJourney']['MonitoredCall']['Extensions'][
                            'Capacities']['EstimatedPassengerCount'])}
                    except KeyError:
                        passengers = {'passengers': 0}

                    try:
                        lineref={'lineref':b['MonitoredVehicleJourney']['LineRef']}
                    except KeyError:
                        lineref = {'lineref': 'n/a'}

                    try:
                        vehicleref={'vehicleref':b['MonitoredVehicleJourney']['VehicleRef']}
                    except KeyError:
                        vehicleref = {'vehicleref': 'n/a'}

                    try:
                        trip_id={'trip_id':b['MonitoredVehicleJourney']['FramedVehicleJourneyRef']['DatedVehicleJourneyRef']}
                    except:
                        trip_id = {'trip_id': 'n/a'}

                    try:
                        next_stop_id={'next_stop_id':b['MonitoredVehicleJourney']['MonitoredCall']['StopPointRef']}
                    except:
                        next_stop_id = {'next_stop_id': 'n/a'}

                    try:
                        next_stop_eta={'next_stop_eta':b['MonitoredVehicleJourney']['MonitoredCall']['ExpectedArrivalTime']}
                    except:
                        next_stop_eta = {'next_stop_eta': 'n/a'}

                    try:
                        next_stop_d_along_route={'next_stop_d_along_route':b['MonitoredVehicleJourney']['MonitoredCall']['Extensions']['Distances'][
                        'CallDistanceAlongRoute']}
                    except:
                        next_stop_d_along_route = {'next_stop_d_along_route': 'n/a'}

                    try:
                        next_stop_d={'next_stop_d':b['MonitoredVehicleJourney']['MonitoredCall']['Extensions']['Distances']['DistanceFromCall']}
                    except:
                        next_stop_d = {'next_stop_d': 'n/a'}


                    f = geojson.Feature(geometry=p, properties={**occupancy,**passengers,**lineref,**trip_id,**vehicleref,**next_stop_id,**next_stop_eta,**next_stop_d_along_route,**next_stop_d})

                    f_list.append(f)
            except KeyError: # no VehicleActivity?
                pass
    fc = geojson.feature.FeatureCollection(f_list)

    with open('./static/lastknownpositions.geojson', 'w') as outfile:
        geojson.dump(fc, outfile)

    return


