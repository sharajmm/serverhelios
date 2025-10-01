from flask import Flask, request, jsonify
import requests
from geopy.distance import geodesic
import os

app = Flask(__name__)

# --- HARDCODED API KEYS FOR HACKATHON DEMO ---
# WARNING: Do not use this method in a production application.
GOOGLE_MAPS_API_KEY = "AIzaSyBLRYiLOgANIIXdHb70lspfXN4p2skEIHI"

# --- Helper function for AI risk scoring ---
def calculate_risk_score(route):
    base_score = 0
    reasons = []
    hazard_coordinates = []

    # Factor 1: Traffic Duration
    duration_in_traffic = route.get("legs", [{}])[0].get("duration_in_traffic", {}).get("value", 0)
    minutes_in_traffic = duration_in_traffic // 60
    if minutes_in_traffic > 5:
        base_score += minutes_in_traffic * 10
        reasons.append(f"High traffic: Approx. {minutes_in_traffic} mins")

    # Factor 2: Hazardous Maneuvers
    hazard_keywords = ["sharp", "roundabout", "merge", "u-turn"]
    maneuver_count = 0
    for step in route.get("legs", [{}])[0].get("steps", []):
        instruction = step.get("html_instructions", "").lower()
        if any(keyword in instruction for keyword in hazard_keywords):
            maneuver_count += 1
            base_score += 100
            hazard_coordinates.append(step.get("start_location"))
    if maneuver_count > 2:
        reasons.append(f"Includes {maneuver_count} complex turns or merges")

    # Factor 3: Accident Blackspots (Coimbatore Data)
    ACCIDENT_BLACKSPOTS = [
        {"lat": 11.0180, "lon": 76.9691, "name": "Gandhipuram Signal"},
        {"lat": 10.9946, "lon": 76.9644, "name": "Ukkadam"},
        {"lat": 11.0268, "lon": 77.0357, "name": "Avinashi Road - Hope College"}
    ]
    blackspot_count = 0
    passed_blackspots = set()
    for step in route.get("legs", [{}])[0].get("steps", []):
        step_loc = step.get("start_location")
        for spot in ACCIDENT_BLACKSPOTS:
            if spot["name"] not in passed_blackspots and geodesic((step_loc['lat'], step_loc['lng']), (spot['lat'], spot['lon'])).meters <= 250:
                base_score += 500
                blackspot_count += 1
                passed_blackspots.add(spot["name"])
    if blackspot_count > 0:
        reasons.append(f"Passes through {blackspot_count} known high-accident zone(s)")
    
    if not reasons:
        reasons.append("This is a standard route. Always ride with caution.")

    return base_score, hazard_coordinates, reasons

# --- API Endpoints ---
@app.route('/api/autocomplete', methods=['GET'])
def autocomplete():
    user_input = request.args.get('input')
    if not user_input:
        return jsonify([])

    google_places_url = f"https://maps.googleapis.com/maps/api/place/autocomplete/json?input={user_input}&components=country:IN&key={GOOGLE_MAPS_API_KEY}"
    try:
        response = requests.get(google_places_url)
        response.raise_for_status()
        data = response.json()
        predictions = data.get("predictions", [])
        descriptions = [p.get("description", "") for p in predictions]
        return jsonify(descriptions)
    except Exception as e:
        return jsonify({"error": f"Autocomplete failed: {str(e)}"}), 500

@app.route('/api/route', methods=['GET'])
def get_route():
    try:
        start_lat = float(request.args.get('start_lat'))
        start_lon = float(request.args.get('start_lon'))
        end_lat = float(request.args.get('end_lat'))
        end_lon = float(request.args.get('end_lon'))
    except (TypeError, ValueError, AttributeError):
        return jsonify({"error": "Invalid or missing coordinate format"}), 400

    params = {
        "origin": f"{start_lat},{start_lon}",
        "destination": f"{end_lat},{end_lon}",
        "key": GOOGLE_MAPS_API_KEY,
        "alternatives": "true",
        "departure_time": "now"
    }
    
    try:
        response = requests.get("https://maps.googleapis.com/maps/api/directions/json", params=params)
        response.raise_for_status()
        data = response.json()

        routes_data = data.get("routes", [])
        if not routes_data:
            return jsonify({"error": "No routes found by Google"}), 404

        route_objects = []
        for route in routes_data:
            raw_score, hazards, reasons = calculate_risk_score(route)
            route_objects.append({
                "polyline": route.get("overview_polyline", {}).get("points"),
                "raw_risk": raw_score,
                "hazards": hazards,
                "reasons": reasons
            })
        
        if not route_objects:
             return jsonify({"error": "Could not process any routes"}), 500

        min_risk = min(r['raw_risk'] for r in route_objects)
        max_risk = max(r['raw_risk'] for r in route_objects)
        
        for route in route_objects:
            if max_risk > min_risk:
                normalized_score = 1 + 9 * (route['raw_risk'] - min_risk) / (max_risk - min_risk)
                route['risk_score'] = round(normalized_score, 1)
            else:
                route['risk_score'] = 1.0
            del route['raw_risk']

        return jsonify({"routes": route_objects})
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({'status': 'healthy', 'message': 'Helios Google-Powered Backend is live!'})