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
    base_score = 100  # Start with base score of 100
    reasons = []
    hazard_coordinates = []

    # Factor 1: Traffic Duration (More granular scoring)
    duration_in_traffic = route.get("legs", [{}])[0].get("duration_in_traffic", {}).get("value", 0)
    normal_duration = route.get("legs", [{}])[0].get("duration", {}).get("value", 0)
    minutes_in_traffic = duration_in_traffic // 60
    normal_minutes = normal_duration // 60
    
    # Calculate traffic delay factor
    if duration_in_traffic > normal_duration and normal_duration > 0:
        delay_factor = (duration_in_traffic - normal_duration) / normal_duration
        traffic_score = min(delay_factor * 200, 300)  # Cap at 300 points
        base_score += traffic_score
        if minutes_in_traffic > normal_minutes + 5:
            reasons.append(f"Heavy traffic: {minutes_in_traffic} mins (normally {normal_minutes} mins)")
        elif minutes_in_traffic > normal_minutes + 2:
            reasons.append(f"Moderate traffic: {minutes_in_traffic} mins (normally {normal_minutes} mins)")
    elif minutes_in_traffic > 15:  # Long route regardless of traffic
        reasons.append(f"Long route: {minutes_in_traffic} minutes travel time")

    # Factor 2: Route Distance (Longer routes = slightly higher base risk)
    distance = route.get("legs", [{}])[0].get("distance", {}).get("value", 0)  # in meters
    distance_km = distance / 1000
    distance_score = min(distance_km * 5, 100)  # 5 points per km, max 100
    base_score += distance_score

    # Factor 3: Hazardous Maneuvers (More nuanced scoring)
    hazard_keywords = {
        "roundabout": 25, "sharp": 35, "u-turn": 45, "merge": 20,
        "exit": 15, "turn left": 8, "turn right": 8, "slight": 5
    }
    maneuver_count = 0
    total_steps = len(route.get("legs", [{}])[0].get("steps", []))
    
    for step in route.get("legs", [{}])[0].get("steps", []):
        instruction = step.get("html_instructions", "").lower()
        for keyword, score in hazard_keywords.items():
            if keyword in instruction:
                maneuver_count += 1
                base_score += score
                if score > 20:  # Only add coordinates for major hazards
                    hazard_coordinates.append(step.get("start_location"))
                break
    
    # Complexity factor based on turns per km
    if distance_km > 0:
        turns_per_km = maneuver_count / distance_km
        if turns_per_km > 10:
            reasons.append(f"Very complex route: {maneuver_count} turns in {distance_km:.1f}km")
        elif turns_per_km > 5:
            reasons.append(f"Complex route: {maneuver_count} turns in {distance_km:.1f}km")
        elif maneuver_count > 15:
            reasons.append(f"Multiple turns required: {maneuver_count} maneuvers")

    # Factor 4: Accident Blackspots (Coimbatore Data)
    ACCIDENT_BLACKSPOTS = [
        {"lat": 11.0180, "lon": 76.9691, "name": "Gandhipuram Signal"},
        {"lat": 10.9946, "lon": 76.9644, "name": "Ukkadam"},
        {"lat": 11.0268, "lon": 77.0357, "name": "Avinashi Road - Hope College"}
    ]
    blackspot_count = 0
    passed_blackspots = set()
    for step in route.get("legs", [{}])[0].get("steps", []):
        step_loc = step.get("start_location")
        if step_loc:
            for spot in ACCIDENT_BLACKSPOTS:
                if spot["name"] not in passed_blackspots and geodesic((step_loc['lat'], step_loc['lng']), (spot['lat'], spot['lon'])).meters <= 250:
                    base_score += 400
                    blackspot_count += 1
                    passed_blackspots.add(spot["name"])
    if blackspot_count > 0:
        reasons.append(f"Passes through {blackspot_count} known high-accident zone(s)")
    
    # Factor 5: Highway vs City Roads
    highway_found = False
    for step in route.get("legs", [{}])[0].get("steps", []):
        instruction = step.get("html_instructions", "").lower()
        if "highway" in instruction or "expressway" in instruction:
            if not highway_found:  # Only add once
                base_score += 50
                reasons.append("Includes highway sections")
                highway_found = True
            break
    
    # Factor 6: Route Type Analysis
    route_steps = route.get("legs", [{}])[0].get("steps", [])
    if len(route_steps) > 20:
        reasons.append(f"Multi-segment route with {len(route_steps)} navigation steps")
    
    # Only add default message if no specific reasons were found
    if not reasons:
        if base_score < 150:
            reasons.append("Direct route with minimal complexity")
        elif base_score < 250:
            reasons.append("Standard city route")
        else:
            reasons.append("Route requires extra caution due to complexity")

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

        # More dynamic risk scoring approach
        if len(route_objects) == 1:
            # Single route: Score based on absolute risk levels
            raw_score = route_objects[0]['raw_risk']
            if raw_score < 150:
                route_objects[0]['risk_score'] = round(1.0 + (raw_score - 100) / 50 * 2, 1)  # 1.0-3.0
            elif raw_score < 300:
                route_objects[0]['risk_score'] = round(3.0 + (raw_score - 150) / 150 * 4, 1)  # 3.0-7.0
            else:
                route_objects[0]['risk_score'] = round(7.0 + min((raw_score - 300) / 200 * 3, 3), 1)  # 7.0-10.0
        else:
            # Multiple routes: Relative scoring with more granularity
            min_risk = min(r['raw_risk'] for r in route_objects)
            max_risk = max(r['raw_risk'] for r in route_objects)
            
            for route in route_objects:
                if max_risk > min_risk:
                    # Use a more nuanced curve that doesn't always hit extremes
                    relative_position = (route['raw_risk'] - min_risk) / (max_risk - min_risk)
                    # Apply a curve that creates more middle values
                    curved_position = 0.5 + 0.5 * (2 * relative_position - 1) ** 3
                    route['risk_score'] = round(2.0 + curved_position * 6.0, 1)  # Range: 2.0-8.0
                else:
                    # All routes have same risk
                    avg_score = sum(r['raw_risk'] for r in route_objects) / len(route_objects)
                    if avg_score < 200:
                        route['risk_score'] = 3.0
                    elif avg_score < 400:
                        route['risk_score'] = 5.0
                    else:
                        route['risk_score'] = 7.0
        
        # Clean up raw scores
        for route in route_objects:
            del route['raw_risk']

        return jsonify({"routes": route_objects})
    except Exception as e:
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

@app.route('/', methods=['GET'])
def home():
    return jsonify({'status': 'healthy', 'message': 'Helios Google-Powered Backend is live!'})