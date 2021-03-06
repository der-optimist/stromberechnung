import requests
import datetime
import json
import time
import socket

read_data_from_ha = True # can be set to false for debugging
send_data_to_ha = True # can be set to false for debugging
debug_output = False

host = socket.gethostname()

if host.lower() == "odroidxu4":
    url_todo = "http://192.168.178.39:8123/api/states/input_boolean.stromverbrauch_todo"
    url_done = "http://192.168.178.39:8123/api/states/input_boolean.stromverbrauch_ist_berechnet"
    url_result_data = "http://192.168.178.39:8123/api/states/sensor.stromverbrauch_tag_extern_berechnet"
    url_data = "http://192.168.178.39:8123/local/stromverbrauch/data.py"
    path_data_local = "/srv/dev-disk-by-uuid-6416d6e7-a248-4554-9d15-d5643a6b8a67/ssddata/omv_scripts/stromverbrauch/data.py"
elif host.lower().startswith("frdc"):
    url_todo = "http://homeassistant.fritz.box:8123/api/states/input_boolean.stromverbrauch_todo"
    url_done = "http://homeassistant.fritz.box:8123/api/states/input_boolean.stromverbrauch_ist_berechnet"
    url_result_data = "http://homeassistant.fritz.box:8123/api/states/sensor.stromverbrauch_tag_extern_berechnet"
    url_data = "http://homeassistant.fritz.box:8123/local/stromverbrauch/data.py"
    path_data_local = "C:\\Users\\F36121\\Desktop\\temp\\\stromberechnung\\data.py"
else:
    print("unknown host, please ad path variables for this host")
    exit()

if read_data_from_ha or send_data_to_ha:
    from ha_token import ha_token # a file named ha_token.py must be placed next to this one, content: ha_token = ABCDEF

def combine_measurements(points_power, points_price_effective, points_price_invoice, start_power, start_price, db_field):
    all_timesteps = []
    price_effective_timesteps = []
    price_effective_values = []
    price_invoice_timesteps = []
    price_invoice_values = []
    power_timesteps = []
    power_values = []
    for price in points_price_effective:
        all_timesteps.append(price["time"])
        price_effective_timesteps.append(price["time"])
        price_effective_values.append(price[db_field])
    for price in points_price_invoice:
        all_timesteps.append(price["time"])
        price_invoice_timesteps.append(price["time"])
        price_invoice_values.append(price[db_field])
    for power in points_power:
        all_timesteps.append(power["time"])
        power_timesteps.append(power["time"])
        power_values.append(power[db_field])
    current_price_effective = start_price
    current_price_invoice = start_price
    current_power = start_power
    total_list = []
    
    for ts in sorted(all_timesteps):
        try:
            current_price_effective = price_effective_values[price_effective_timesteps.index(ts)]
        except ValueError:
            pass
        try:
            current_price_invoice = price_invoice_values[price_invoice_timesteps.index(ts)]
        except ValueError:
            pass
        try:
            current_power = power_values[power_timesteps.index(ts)]
        except ValueError:
            pass
    
        ts_dict = {"time":ts, "price_effective":current_price_effective, "price_invoice":current_price_invoice, "power":current_power}
        total_list.append(ts_dict)
    return total_list

if read_data_from_ha:
    ha_auth = "Bearer " + ha_token
    headers = {
        "Authorization": ha_auth,
        "content-type": "application/json",
    }
    
    response = requests.get(url_todo, headers=headers)
    state_todo = response.json()["state"]
else:
    state_todo = "on"

if state_todo == "on":
    ts_start_calculation = datetime.datetime.now().timestamp()
    
    if read_data_from_ha:
        # load input data
        if debug_output:
            print("loading data from ha")
        r = requests.get(url_data, allow_redirects=True)
        open(path_data_local, 'wb').write(r.content)
        
        # reset "todo" flag
        if debug_output:
            print("reseting todo flag in ha")
        data = {"state": "off"}
        response = requests.post(url_todo, headers=headers, data=json.dumps(data))
        if debug_output:
            print("response: {}".format(response.text))
    
    # import input data
    from data import date_str, power_sensors, price_pv_effective_points, price_pv_invoice_points, start_power, start_price, db_field, ts_start_local, ts_end_local, utc_offset_timestamp

    dict_results = dict()
    for sensor in power_sensors.keys():
        if debug_output:
            print("Calculating sensor: {}".format(sensor))
        steps_combined = combine_measurements(power_sensors[sensor], price_pv_effective_points, price_pv_invoice_points, start_power, start_price, db_field)
        consumption_Ws = 0.0
        cost_effective = 0.0
        cost_invoice = 0.0
        past_timestep = ts_end_local
        start_time_reached = False
        for point in steps_combined[::-1]:
            try:
                timestamp_local = datetime.datetime.strptime(point["time"], '%Y-%m-%dT%H:%M:%S.%fZ').timestamp() + utc_offset_timestamp
            except ValueError:
                timestamp_local = datetime.datetime.strptime(point["time"], '%Y-%m-%dT%H:%M:%SZ').timestamp() + utc_offset_timestamp
            if timestamp_local < ts_start_local:
                timestamp_local = ts_start_local
                start_time_reached = True
            time_delta_s = past_timestep - timestamp_local
            energy_Ws = point["power"] * time_delta_s
            price_pv_effective = point["price_effective"]
            price_pv_invoice = point["price_invoice"]
            cost_effective = cost_effective + price_pv_effective * energy_Ws / 3600000
            cost_invoice = cost_invoice + price_pv_invoice * energy_Ws / 3600000
            consumption_Ws = consumption_Ws + energy_Ws
            past_timestep = timestamp_local
            if start_time_reached:
                break
        consumption_kWh = consumption_Ws / 3600000
        cost_without_pv = consumption_kWh * start_price
        dict_results[sensor] = {"consumption_kWh":consumption_kWh, "cost_without_pv":cost_without_pv, "cost_effective":cost_effective, "cost_invoice":cost_invoice}
        
        if debug_output:
            print("{}: {}".format(sensor,dict_results[sensor]))
    
    if send_data_to_ha:
        # send result to HA
        result_state = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        result_attributes = {"date_str":date_str, "dict_results":dict_results}
        data = {"state": result_state, "attributes": result_attributes}
        response = requests.post(url_result_data, headers=headers, data=json.dumps(data))
        if debug_output:
            print("\nSending result to HA")
            print(response.text)
        
        # set result-finished-flag
        if debug_output:
            print("will set result-finished-flag in a few seconds")
        time.sleep(10)
        data = {"state": "on"}
        response = requests.post(url_done, headers=headers, data=json.dumps(data))
        if debug_output:
            print("\nSetting the done flag in HA")
            print(response.text)
    
    if debug_output:
        print("Time total: {}".format(datetime.datetime.now().timestamp() - ts_start_calculation))
