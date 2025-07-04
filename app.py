# app.py
import gevent.monkey

gevent.monkey.patch_all()  # Wichtig: Frühzeitiges Patching für Stabilität mit gevent

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from models import init_db, SessionLocal, Player, Route, PokemonCatch, GlobalOrder, LevelCap, reset_full_db
import json
import os
import threading
import time
import socket

app = Flask(__name__)
app.config['SECRET_KEY'] = 'YOUR_SUPER_SECRET_KEY_HERE_CHANGE_THIS_IN_PRODUCTION'  # Wichtig: In Produktion ändern!
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')  # async_mode auf 'gevent' setzen

# --- Globale Config-Verwaltung ---
# Verwenden wir ein Dictionary, das wir neu laden können
_app_config_data = {
    "ALL_ROUTES": [],
    "ALL_POKEMON_NAMES": []
}


def _load_json_data_internal(filename):
    """Interne Hilfsfunktion zum Laden einer JSON-Datei, auch für Config-Endpunkte."""
    # Verwende os.path.abspath(__file__) um den absoluten Pfad des Skripts zu erhalten
    # und dann os.path.join, um den vollständigen Pfad zur JSON-Datei zu bilden.
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        if not os.path.exists(filepath):
            print(f"Warning: {filename} not found at {filepath}. Returning empty list.")
            return []
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {filename} at {filepath}. File might be corrupted.")
        return []
    except Exception as e:
        print(f"An unexpected error occurred while loading {filename} from {filepath}: {e}")
        return []


def reload_app_configs():
    """Lädt die globalen Konfigurationen (Routen, Pokemon-Namen) neu."""
    print("Reloading application configurations (ALL_ROUTES, ALL_POKEMON_NAMES)...")
    _app_config_data["ALL_ROUTES"] = [item['name'] for item in _load_json_data_internal('routes.json')]
    _app_config_data["ALL_POKEMON_NAMES"] = [item['name'] for item in _load_json_data_internal('pokemon_names.json')]
    print(
        f"Loaded {len(_app_config_data['ALL_ROUTES'])} routes and {len(_app_config_data['ALL_POKEMON_NAMES'])} pokemon names.")


# Lade die Configs beim App-Start initial
reload_app_configs()

# Jetzt greifen wir auf die globalen Daten über _app_config_data zu
# Diese Variablen werden beim initialen Start gesetzt und dann über _app_config_data aktualisiert
# Sie sind im Grunde nur Aliasse für die Daten in _app_config_data
ALL_ROUTES = _app_config_data["ALL_ROUTES"]
ALL_POKEMON_NAMES = _app_config_data["ALL_POKEMON_NAMES"]

# --- Datenbank-Initialisierung beim Start der App ---
with app.app_context():
    init_db()


# --- Hilfsfunktion für Datenbank-Session ---
def get_db_session():
    return SessionLocal()


# --- Routen für HTML-Seiten ---
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/summary')
def summary():
    return render_template('summary.html')


# --- API Routen (bestehende) ---
@app.route('/api/data')
def get_all_data():
    session = get_db_session()
    # Wichtig: Routen nach ID sortieren, um die Einfügereihenfolge zu behalten
    routes = session.query(Route).order_by(Route.id).all()
    players = session.query(Player).all()
    catches = session.query(PokemonCatch).all()
    global_orders = session.query(GlobalOrder).all()
    level_caps = session.query(LevelCap).all()

    players_data = [{'id': p.id, 'name': p.name} for p in players]
    routes_data = [{'id': r.id, 'name': r.name, 'status': r.status} for r in routes]
    catches_data = [
        {'player_id': c.player_id, 'route_id': c.route_id, 'pokemon_name': c.pokemon_name}
        for c in catches
    ]
    global_orders_data = [
        {'order_number': go.order_number, 'is_obtained': go.is_obtained}
        for go in global_orders
    ]
    level_caps_data = [
        {'name': lc.name, 'order_number': lc.order_number, 'max_level': lc.max_level,
         'adjusted_level': lc.adjusted_level}
        for lc in level_caps
    ]

    session.close()
    return jsonify({
        'players': players_data,
        'routes': routes_data,
        'catches': catches_data,
        'global_orders': global_orders_data,
        'level_caps': level_caps_data,
        'all_pokemon_names': _app_config_data["ALL_POKEMON_NAMES"],  # Greife auf die neu ladbare Config zu
        'all_route_names': _app_config_data["ALL_ROUTES"],  # Greife auf die neu ladbare Config zu
    })


@app.route('/api/add_player', methods=['POST'])
def add_player():
    data = request.json
    player_name = data.get('name')
    if not player_name:
        return jsonify({'error': 'Spielername fehlt'}), 400

    session = get_db_session()
    try:
        existing_player = session.query(Player).filter_by(name=player_name).first()
        if existing_player:
            return jsonify({'error': 'Spieler existiert bereits'}), 409

        new_player = Player(name=player_name)
        session.add(new_player)
        session.commit()

        routes = session.query(Route).all()
        for route in routes:
            session.add(PokemonCatch(player_id=new_player.id, route_id=route.id, pokemon_name=None))
        session.commit()

        socketio.emit('player_added', {'id': new_player.id, 'name': new_player.name})
        return jsonify(
            {'message': 'Spieler hinzugefügt', 'player': {'id': new_player.id, 'name': new_player.name}}), 201
    except Exception as e:
        session.rollback()
        print(f"Fehler beim Hinzufügen des Spielers: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500
    finally:
        session.close()


@app.route('/api/add_route', methods=['POST'])
def add_route():
    data = request.json
    route_name = data.get('name')
    if not route_name:
        return jsonify({'error': 'Routenname fehlt'}), 400

    session = get_db_session()
    try:
        existing_route = session.query(Route).filter_by(name=route_name).first()
        if existing_route:
            return jsonify({'error': 'Route existiert bereits'}), 409

        new_route = Route(name=route_name, status="")
        session.add(new_route)
        session.commit()

        players = session.query(Player).all()
        for player in players:
            session.add(PokemonCatch(player_id=player.id, route_id=new_route.id, pokemon_name=None))
        session.commit()

        socketio.emit('route_added', {'id': new_route.id, 'name': new_route.name, 'status': new_route.status})
        return jsonify({'message': 'Route hinzugefügt',
                        'route': {'id': new_route.id, 'name': new_route.name, 'status': new_route.status}}), 201
    except Exception as e:
        session.rollback()
        print(f"Fehler beim Hinzufügen der Route: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500
    finally:
        session.close()


@app.route('/api/update_catch', methods=['POST'])
def update_catch():
    data = request.json
    player_id = data.get('player_id')
    route_id = data.get('route_id')
    pokemon_name = data.get('pokemon_name')

    if not all([player_id, route_id]):
        return jsonify({'error': 'Spieler-ID oder Routen-ID fehlt'}), 400

    session = get_db_session()
    try:
        catch_entry = session.query(PokemonCatch).filter_by(player_id=player_id, route_id=route_id).first()

        if not catch_entry:
            catch_entry = PokemonCatch(player_id=player_id, route_id=route_id, pokemon_name=pokemon_name)
            session.add(catch_entry)
        else:
            catch_entry.pokemon_name = pokemon_name

        session.commit()

        socketio.emit('catch_updated', {
            'player_id': player_id,
            'route_id': route_id,
            'pokemon_name': pokemon_name
        })
        return jsonify({'message': 'Fang aktualisiert'}), 200
    except Exception as e:
        session.rollback()
        print(f"Fehler beim Aktualisieren des Fangs: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500
    finally:
        session.close()


@app.route('/api/toggle_global_order', methods=['POST'])
def toggle_global_order():
    data = request.json
    order_number = data.get('order_number')

    try:
        order_number = int(order_number)
    except (TypeError, ValueError):
        return jsonify({'error': 'Ungültige Ordensnummer bereitgestellt.'}), 400

    session = get_db_session()
    try:
        order_entry = session.query(GlobalOrder).filter_by(order_number=order_number).first()

        if not order_entry:
            return jsonify({'error': f'Orden/Meilenstein mit Nummer {order_number} nicht gefunden.'}), 404

        order_entry.is_obtained = not order_entry.is_obtained
        session.commit()

        socketio.emit('global_order_toggled', {
            'order_number': order_number,
            'is_obtained': order_entry.is_obtained
        })
        return jsonify({'message': 'Globaler Orden-Status aktualisiert', 'is_obtained': order_entry.is_obtained}), 200
    except Exception as e:
        session.rollback()
        print(f"Fehler beim Umschalten des globalen Orden-Status: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500
    finally:
        session.close()


@app.route('/api/update_route_status', methods=['POST'])
def update_route_status():
    data = request.json
    route_id = data.get('route_id')
    status_text = data.get('status_text', "")

    if route_id is None:
        return jsonify({'error': 'Routen-ID fehlt.'}), 400

    session = get_db_session()
    try:
        route_entry = session.query(Route).filter_by(id=route_id).first()
        if not route_entry:
            return jsonify({'error': f'Route mit ID {route_id} nicht gefunden.'}), 404

        route_entry.status = status_text
        session.commit()

        socketio.emit('route_status_updated', {'route_id': route_id, 'status_text': status_text})
        return jsonify({'message': 'Routenstatus aktualisiert', 'route_id': route_id, 'status_text': status_text}), 200
    except Exception as e:
        session.rollback()
        print(f"Fehler beim Aktualisieren des Routenstatus: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500
    finally:
        session.close()


@app.route('/api/reset_all_data', methods=['POST'])
def reset_all_data():
    session = get_db_session()
    try:
        session.query(PokemonCatch).update({PokemonCatch.pokemon_name: None})
        session.query(Route).update({Route.status: ""})
        session.commit()
        socketio.emit('all_data_reset')
        return jsonify({'message': 'Alle Pokémon-Fänge und Routen-Stati zurückgesetzt.'}), 200
    except Exception as e:
        session.rollback()
        print(f"Fehler beim Zurücksetzen aller Daten: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500
    finally:
        session.close()


@app.route('/api/clear_route_data', methods=['POST'])
def clear_route_data():
    data = request.json
    route_id = data.get('route_id')

    if route_id is None:
        return jsonify({'error': 'Routen-ID fehlt.'}), 400

    session = get_db_session()
    try:
        route_to_delete = session.query(Route).filter_by(id=route_id).first()
        if not route_to_delete:
            return jsonify({'error': f'Route mit ID {route_id} nicht gefunden.'}), 404

        session.delete(route_to_delete)
        session.commit()
        socketio.emit('route_deleted', {'route_id': route_id})
        return jsonify({'message': f'Route {route_to_delete.name} und zugehörige Daten gelöscht.'}), 200
    except Exception as e:
        session.rollback()
        print(f"Fehler beim Löschen der Route: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500
    finally:
        session.close()


@app.route('/api/full_db_reset', methods=['POST'])
def full_db_reset():
    try:
        reset_full_db()
        socketio.emit('full_db_reset')
        return jsonify({'message': 'Datenbank vollständig zurückgesetzt.'}), 200
    except Exception as e:
        print(f"Fehler beim vollständigen Datenbank-Reset: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500


# NEUE API-ENDPUNKTE FÜR KONFIGURATIONSVERWALTUNG
@app.route('/api/config/<filename>', methods=['GET'])
def get_config_file(filename):
    """Gibt den Inhalt einer JSON-Konfigurationsdatei zurück."""
    if filename not in ['routes.json', 'pokemon_names.json', 'level_caps.json']:
        return jsonify({'error': 'Unbekannte Konfigurationsdatei'}), 400

    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'content': content}), 200
    except FileNotFoundError:
        return jsonify({'error': f'Datei {filename} nicht gefunden'}), 404
    except Exception as e:
        return jsonify({'error': f'Fehler beim Lesen von {filename}: {str(e)}'}), 500


@app.route('/api/config/<filename>', methods=['POST'])
def save_config_file(filename):
    """Speichert den Inhalt einer JSON-Konfigurationsdatei."""
    if filename not in ['routes.json', 'pokemon_names.json', 'level_caps.json']:
        return jsonify({'error': 'Unbekannte Konfigurationsdatei'}), 400

    data = request.json
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'Inhalt fehlt'}), 400

    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        # Versuche, den JSON-Inhalt zu parsen, um Syntaxfehler zu vermeiden
        json.loads(content)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        # NEU: Lade Konfigurationen nach dem Speichern neu
        if filename in ['routes.json', 'pokemon_names.json']:
            reload_app_configs()  # Lädt nur die in-memory Listen neu

        socketio.emit('config_saved', {'filename': filename})  # SocketIO-Event senden
        return jsonify({'message': f'Datei {filename} erfolgreich gespeichert.'}), 200
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Ungültiges JSON-Format in {filename}: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'Fehler beim Schreiben von {filename}: {str(e)}'}), 500


@app.route('/api/reload_configs', methods=['POST'])
def reload_configs_api():
    """Trigger zum Neuladen der Konfigurationsdateien."""
    reload_app_configs()
    socketio.emit('configs_reloaded')  # SocketIO-Event senden
    return jsonify({'message': 'App-Konfigurationen neu geladen.'}), 200


@socketio.on('connect')
def handle_connect():
    print('Client verbunden!')


@socketio.on('disconnect')
def handle_disconnect():
    print('Client getrennt!')


if __name__ == '__main__':
    FLASK_HOST = "0.0.0.0"
    FLASK_PORT = 5000


    def open_browser_after_start():
        import webbrowser
        import time
        time.sleep(1.5)
        try:
            webbrowser.open_new(f"http://127.0.0.1:{FLASK_PORT}/")
        except Exception as e:
            print(f"Fehler beim automatischen Öffnen des Browsers: {e}")


    threading.Thread(target=open_browser_after_start).start()

    socketio.run(app, debug=True, host=FLASK_HOST, port=FLASK_PORT)