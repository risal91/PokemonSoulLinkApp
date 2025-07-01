# models.py
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import json
import os

Base = declarative_base()

# --- Datenbank-Modelle ---

class Player(Base):
    """Repräsentiert einen Teilnehmer der Soul Link Challenge."""
    __tablename__ = 'players'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    pokemon_catches = relationship('PokemonCatch', back_populates='player', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Player(id={self.id}, name='{self.name}')>"

class Route(Base):
    """Repräsentiert eine Route im Spiel."""
    __tablename__ = 'routes'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    status = Column(String, default="") # Statusfeld für die Route

    pokemon_catches = relationship('PokemonCatch', back_populates='route', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Route(id={self.id}, name='{self.name}', status='{self.status}')>"

class PokemonCatch(Base):
    """Repräsentiert ein gefangenes Pokémon eines Spielers auf einer bestimmten Route."""
    __tablename__ = 'pokemon_catches'
    id = Column(Integer, primary_key=True)
    pokemon_name = Column(String, nullable=True) # Kann leer sein, wenn noch nichts gefangen wurde

    player_id = Column(Integer, ForeignKey('players.id'), nullable=False)
    route_id = Column(Integer, ForeignKey('routes.id'), nullable=False)

    player = relationship('Player', back_populates='pokemon_catches')
    route = relationship('Route', back_populates='pokemon_catches')

    def __repr__(self):
        return f"<PokemonCatch(id={self.id}, player_id={self.player_id}, route_id={self.route_id}, pokemon='{self.pokemon_name}')>"

class GlobalOrder(Base):
    """Repräsentiert den globalen Status eines Ordens (für alle Spieler)."""
    __tablename__ = 'global_orders'
    id = Column(Integer, primary_key=True)
    order_number = Column(Integer, unique=True, nullable=False) # 1 bis 8, oder spezielle IDs für Top 4/Champ
    is_obtained = Column(Boolean, default=False) # True, wenn der Orden/Meilenstein erreicht wurde

    def __repr__(self):
        return f"<GlobalOrder(id={self.id}, order_number={self.order_number}, obtained={self.is_obtained})>"

class LevelCap(Base):
    """Repräsentiert das Level-Cap für einen bestimmten Meilenstein (Orden, Top 4, Champ)."""
    __tablename__ = 'level_caps'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False) # Z.B. "1. Arena", "Top 4 (1)", "Champ"
    order_number = Column(Integer, unique=True, nullable=False) # UNIQUE, da dies der Identifikator ist
    max_level = Column(Integer, nullable=False)
    adjusted_level = Column(Integer, nullable=False) # Max Level - 2

    def __repr__(self):
        return f"<LevelCap(id={self.id}, name='{self.name}', order_number={self.order_number}, max_level={self.max_level}, adjusted_level={self.adjusted_level})>"


# --- Datenbank-Initialisierung ---

DATABASE_URL = "sqlite:///soul_link_challenge.db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Level Cap Daten aus JSON laden
def load_json_data(file_path): # Umbenannt von load_level_caps_from_json, da jetzt universell
    try:
        # Pfad korrigieren, um relativ zum Skript zu sein
        base_dir = os.path.dirname(os.path.abspath(__file__))
        full_file_path = os.path.join(base_dir, file_path)

        if not os.path.exists(full_file_path) or os.path.getsize(full_file_path) == 0:
            print(f"Warning: '{file_path}' not found or empty. Returning empty list. Please create/fill this file.")
            # Optional: Leere Datei erstellen
            with open(full_file_path, 'w', encoding='utf-8') as f:
                json.dump([], f)
            return []
        with open(full_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Zusätzliche Validierung für LevelCaps
            if 'level_caps' in file_path:
                for item in data:
                    if 'order_number' not in item:
                        print(f"Warning: Missing 'order_number' in a level cap entry in {file_path}. Skipping entry: {item}")
                return [item for item in data if 'order_number' in item]
            return data
    except FileNotFoundError:
        print(f"Fehler: '{file_path}' nicht gefunden. Bitte erstelle die Datei.")
        return []
    except json.JSONDecodeError:
        print(f"Fehler: '{file_path}' ist keine gültige JSON-Datei oder ist leer. Inhalt: {open(full_file_path, 'r', encoding='utf-8').read()[:200]}...")
        return []
    except Exception as e:
        print(f"An unexpected error occurred while loading '{file_path}' from JSON: {e}")
        return []


def init_db():
    """Erstellt alle Tabellen in der Datenbank, falls sie noch nicht existieren."""
    Base.metadata.create_all(bind=engine)
    print("Datenbanktabellen erstellt oder aktualisiert.")

    session = SessionLocal()
    try:
        # Füge Standard-Level-Caps hinzu, falls noch nicht vorhanden
        level_cap_data = load_json_data('level_caps.json')
        for item in level_cap_data:
            if not session.query(LevelCap).filter_by(order_number=item['order_number']).first():
                session.add(LevelCap(
                    name=item['name'],
                    order_number=item['order_number'],
                    max_level=item['max_level'],
                    adjusted_level=item['adjusted_level']
                ))
        session.commit()
        print("Standard-Level-Caps aus JSON hinzugefügt.")

        # Initialisiere globale Orden, falls noch nicht vorhanden
        order_milestones = [
            {"number": 1, "name": "1. Arena"}, {"number": 2, "name": "2. Arena"},
            {"number": 3, "name": "3. Arena"}, {"number": 4, "name": "4. Arena"},
            {"number": 5, "name": "5. Arena"}, {"number": 6, "name": "6. Arena"},
            {"number": 7, "name": "7. Arena"}, {"number": 8, "name": "8. Arena"},
            {"number": 9, "name": "Top 4 (1)"}, {"number": 10, "name": "Top 4 (2)"},
            {"number": 11, "name": "Top 4 (3)"}, {"number": 12, "name": "Top 4 (4)"},
            {"number": 13, "name": "Champ"}
        ]
        for milestone in order_milestones:
            if not session.query(GlobalOrder).filter_by(order_number=milestone["number"]).first():
                session.add(GlobalOrder(order_number=milestone["number"], is_obtained=False))
        session.commit()
        print("Globale Orden/Meilensteine hinzugefügt.")

    except Exception as e:
        session.rollback()
        print(f"Fehler beim Initialisieren der Datenbank: {e}")
    finally:
        session.close()

def reset_full_db():
    """Löscht alle Tabellen und initialisiert die Datenbank neu."""
    print("Starte vollständigen Datenbank-Reset...")
    Base.metadata.drop_all(bind=engine) # Löscht alle Tabellen
    print("Alle Datenbanktabellen gelöscht.")
    init_db() # Initialisiert sie neu mit Standarddaten
    print("Datenbank vollständig zurückgesetzt und neu initialisiert.")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()