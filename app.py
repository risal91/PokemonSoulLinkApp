# app.py
import gevent.monkey
gevent.monkey.patch_all()

from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from models import init_db, SessionLocal, Player, Route, PokemonCatch, GlobalOrder, LevelCap, reset_full_db
import json
import os
import threading
import time
import socket
import zipfile
import io
import sqlite3 # Für SQLite-Datenbank-Interaktion

# --- App Konfiguration ---
app = Flask(__name__)
# WICHTIG: SECRET_KEY IN PRODUKTION ZU EINEM ZUFÄLLIGEN, LANGEN STRING ÄNDERN!
app.config['SECRET_KEY'] = 'YOUR_SUPER_SECRET_KEY_HERE_CHANGE_THIS_IN_PRODUCTION' 
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# --- Globale App-Konfigurationsdaten (aus JSONs) ---
_app_config_data = {
    "ALL_ROUTES": [],
    "ALL_POKEMON_NAMES": []
}

def _load_json_data_internal(filename):
    """Interne Hilfsfunktion zum Laden einer JSON-Datei."""
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
    """Lädt die globalen Konfigurationen (Routen, Pokemon-Namen) neu aus JSON-Dateien."""
    print("Reloading application configurations (ALL_ROUTES, ALL_POKEMON_NAMES)...")
    _app_config_data["ALL_ROUTES"] = [item['name'] for item in _load_json_data_internal('routes.json')]
    _app_config_data["ALL_POKEMON_NAMES"] = [item['name'] for item in _load_json_data_internal('pokemon_names.json')]
    print(f"Loaded {len(_app_config_data['ALL_ROUTES'])} routes and {len(_app_config_data['ALL_POKEMON_NAMES'])} pokemon names.")
    
# Lade die Configs beim App-Start initial
reload_app_configs()

# Diese Variablen verweisen auf die Listen in _app_config_data
ALL_ROUTES = _app_config_data["ALL_ROUTES"]
ALL_POKEMON_NAMES = _app_config_data["ALL_POKEMON_NAMES"]

# --- Datenbank-Initialisierung beim Start der App ---
with app.app_context():
    init_db()

# --- Hilfsfunktion für Datenbank-Session ---
def get_db_session():
    return SessionLocal()

# --- Admin-Passwort für den Import-String ---
ADMIN_IMPORT_PASSWORD = "ImportJetzt" # <-- DEIN PASSWORT HIER! Wichtig: Sicheres Passwort wählen!

# --- Routen für HTML-Seiten ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/summary')
def summary():
    return render_template('summary.html')

# --- API Routen (bestehende, unverändert) ---
@app.route('/api/data')
def get_all_data():
    session = get_db_session()
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
        {'name': lc.name, 'order_number': lc.order_number, 'max_level': lc.max_level, 'adjusted_level': lc.adjusted_level}
        for lc in level_caps
    ]

    session.close()
    return jsonify({
        'players': players_data,
        'routes': routes_data,
        'catches': catches_data,
        'global_orders': global_orders_data,
        'level_caps': level_caps_data,
        'all_pokemon_names': _app_config_data["ALL_POKEMON_NAMES"],
        'all_route_names': _app_config_data["ALL_ROUTES"],
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
        return jsonify({'message': 'Spieler hinzugefügt', 'player': {'id': new_player.id, 'name': new_player.name}}), 201
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
        return jsonify({'message': 'Route hinzugefügt', 'route': {'id': new_route.id, 'name': new_route.name, 'status': new_route.status}}), 201
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
        reload_app_configs() 
        socketio.emit('full_db_reset')
        return jsonify({'message': 'Datenbank vollständig zurückgesetzt.'}), 200
    except Exception as e:
        print(f"Fehler beim vollständigen Datenbank-Reset: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500


# --- API-ENDPUNKTE FÜR KONFIGURATIONSVERWALTUNG ---
@app.route('/api/config/<filename>', methods=['GET'])
def get_config_file(filename):
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
    if filename not in ['routes.json', 'pokemon_names.json', 'level_caps.json']:
        return jsonify({'error': 'Unbekannte Konfigurationsdatei'}), 400
    
    data = request.json
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'Inhalt fehlt'}), 400

    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        json.loads(content) 
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        if filename in ['routes.json', 'pokemon_names.json']:
            reload_app_configs()

        socketio.emit('config_saved', {'filename': filename})
        return jsonify({'message': f'Datei {filename} erfolgreich gespeichert.'}), 200
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Ungültiges JSON-Format in {filename}: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'Fehler beim Schreiben von {filename}: {str(e)}'}), 500

@app.route('/api/reload_configs', methods=['POST'])
def reload_configs_api():
    """Trigger zum Neuladen der Konfigurationsdateien."""
    reload_app_configs()
    socketio.emit('configs_reloaded')
    return jsonify({'message': 'App-Konfigurationen neu geladen.'}), 200

# --- API-ENDPUNKTE FÜR DATEI-BACKUP UND DATEI-RESTORE (ZIP-Datei) ---
@app.route('/api/backup', methods=['GET'])
def backup_data_zip():
    """Erstellt ein ZIP-Archiv mit Datenbank und JSON-Konfigurationsdateien zum Download."""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, 'soul_link_challenge.db')
        
        json_filenames = ['routes.json', 'pokemon_names.json', 'level_caps.json']
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            if os.path.exists(db_path):
                zip_file.write(db_path, os.path.basename(db_path))
            else:
                print(f"Warning: Database file not found at {db_path} during backup.")

            for filename in json_filenames:
                file_path = os.path.join(base_dir, filename)
                if os.path.exists(file_path):
                    zip_file.write(file_path, os.path.basename(file_path))
                else:
                    print(f"Warning: JSON file {filename} not found at {file_path} during backup.")

        zip_buffer.seek(0)
        return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name='pokemon_soul_link_backup.zip')

    except Exception as e:
        print(f"Fehler beim Erstellen des ZIP-Backups: {e}")
        return jsonify({'error': f'Fehler beim Erstellen des ZIP-Backups: {str(e)}'}), 500

@app.route('/api/restore', methods=['POST'])
def restore_data_zip():
    """Lädt ein ZIP-Archiv hoch, extrahiert es und ersetzt Datenbank/JSON-Dateien."""
    if 'backup_file' not in request.files:
        return jsonify({'error': 'Keine Datei hochgeladen'}), 400
    
    backup_file = request.files['backup_file']
    if backup_file.filename == '':
        return jsonify({'error': 'Leere Datei hochgeladen'}), 400
    
    if not backup_file.filename.lower().endswith('.zip'):
        return jsonify({'error': 'Nur ZIP-Dateien sind erlaubt'}), 400

    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    try:
        zip_buffer_in = io.BytesIO(backup_file.read())
        
        with zipfile.ZipFile(zip_buffer_in, 'r') as zip_ref:
            allowed_base_filenames = ['soul_link_challenge.db', 'routes.json', 'pokemon_names.json', 'level_caps.json']

            for member in zip_ref.namelist():
                member_filename = os.path.basename(member) 
                
                if member_filename not in allowed_base_filenames:
                    raise Exception(f"Unerlaubte Datei in ZIP-Archiv: {member_filename}")
                
                target_path = os.path.join(base_dir, member_filename)
                with open(target_path, "wb") as outfile:
                    outfile.write(zip_ref.read(member))

        SessionLocal().close_all() 
        reload_app_configs() 

        socketio.emit('restore_completed')
        return jsonify({'message': 'Daten erfolgreich wiederhergestellt.'}), 200
    except zipfile.BadZipFile:
        return jsonify({'error': 'Ungültiges ZIP-Archiv.'}), 400
    except Exception as e:
        print(f"Fehler beim Wiederherstellen der Daten: {e}")
        return jsonify({'error': f'Interner Serverfehler: {str(e)}'}), 500

# NEUE API-ENDPUNKTE FÜR STRING-EXPORT/IMPORT
@app.route('/api/export_all_data_string', methods=['GET'])
def export_all_data_string():
    """Exportiert alle Konfigurations-JSONs und den DB-Inhalt (SQL-Dump) als JSON-String."""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 1. JSON-Dateien lesen
        config_data = {}
        json_filenames = ['routes.json', 'pokemon_names.json', 'level_caps.json']
        for filename in json_filenames:
            filepath = os.path.join(base_dir, filename)
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    config_data[filename] = f.read()
            else:
                config_data[filename] = "[]" # Standardleerer JSON-Array als String
        
        # 2. SQLite-Datenbank dumpen
        db_path = os.path.join(base_dir, 'soul_link_challenge.db')
        if not os.path.exists(db_path):
            return jsonify({'error': 'Datenbankdatei nicht gefunden für Export.'}), 404
        
        conn = None
        db_dump_sql = ""
        try:
            conn = sqlite3.connect(db_path)
            # Use .iterdump() for a complete SQL dump
            db_dump_sql = "\n".join(conn.iterdump())
        except Exception as e:
            print(f"Fehler beim Erstellen des DB-Dumps: {e}")
            return jsonify({'error': f'Fehler beim Erstellen des DB-Dumps: {str(e)}'}), 500
        finally:
            if conn:
                conn.close()

        # 3. Alles in einem JSON-Objekt zusammenfassen
        all_data = {
            "config": config_data,
            "database_dump_sql": db_dump_sql
        }
        
        return jsonify(all_data), 200 # Gebe es als JSON zurück
    except Exception as e:
        print(f"Fehler beim Exportieren aller Daten als String: {e}")
        return jsonify({'error': f'Fehler beim Exportieren: {str(e)}'}), 500

@app.route('/api/import_all_data_string', methods=['POST'])
def import_all_data_string():
    """Importiert alle Konfigurations-JSONs und den DB-Inhalt (SQL-Dump) aus einem JSON-String."""
    data = request.json
    import_string = data.get('data_string')
    password = data.get('password') # <-- Passwort hier abfragen

    if not import_string or not password:
        return jsonify({'error': 'Daten-String oder Passwort fehlen.'}), 400
    
    # Passwort-Prüfung
    if password != ADMIN_IMPORT_PASSWORD:
        return jsonify({'error': 'Falsches Passwort.'}), 403 # 403 Forbidden
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, 'soul_link_challenge.db')

    try:
        parsed_data = json.loads(import_string)
        config_data = parsed_data.get('config', {})
        db_dump_sql = parsed_data.get('database_dump_sql', "")

        # 1. JSON-Konfigurationsdateien schreiben
        json_filenames = ['routes.json', 'pokemon_names.json', 'level_caps.json']
        for filename in json_filenames:
            if filename in config_data:
                filepath = os.path.join(base_dir, filename)
                try:
                    json.loads(config_data[filename]) 
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(config_data[filename])
                except json.JSONDecodeError:
                    raise Exception(f"Ungültiges JSON-Format für {filename} im Import-String.")
                except Exception as e:
                    raise Exception(f"Fehler beim Schreiben von {filename} während des Imports: {str(e)}")

        # 2. Datenbank wiederherstellen
        SessionLocal().close_all() # Schließe alle aktiven DB-Sessions

        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"Alte Datenbankdatei {db_path} gelöscht.")
        
        # Datenbank neu initialisieren (Schema wird erstellt)
        init_db() 
        
        # Führe den SQL-Dump aus, um die Daten einzufügen
        if db_dump_sql:
            conn = None
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.executescript(db_dump_sql) # Führt alle SQL-Befehle aus
                conn.commit()
                print("Datenbank aus SQL-Dump wiederhergestellt.")
            except Exception as e:
                # WICHTIG: Wenn der Dump fehlschlägt, ist die DB möglicherweise inkonsistent
                print(f"Fehler beim Ausführen des SQL-Dumps: {e}")
                raise Exception(f"Fehler beim Ausführen des SQL-Dumps: {str(e)}")
            finally:
                if conn:
                    conn.close()

        # 3. App-Konfigurationen im Speicher neu laden
        reload_app_configs()

        socketio.emit('import_completed') # SocketIO-Event senden
        return jsonify({'message': 'Daten erfolgreich aus String wiederhergestellt.'}), 200
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Ungültiges JSON-Format des Import-Strings: {str(e)}'}), 400
    except Exception as e:
        print(f"Fehler beim Importieren aller Daten aus String: {e}")
        return jsonify({'error': f'Fehler beim Importieren: {str(e)}'}), 500

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