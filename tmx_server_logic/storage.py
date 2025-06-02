"""
This module, `storage.py`, provides the `SQLiteStore` class, responsible for
all database interactions for the TMX server. It handles persistence and retrieval
of TMX data, including TMX file entries, headers, translation units (TUs),
and translation unit variants (TUVs/segments), using an SQLite database.

The `SQLiteStore` class abstracts SQLite operations, offering methods to create, read,
update, and delete TMX components. It manages its own database connection and includes
functionality to initialize the database schema. It is designed to support
operations scoped to specific TMX files when a `tmx_file_id` is provided.

Key features:
- Database initialization and schema creation.
- Management of TMX file entries.
- CRUD operations for TMX header, TUs, and TUVs, typically scoped by `tmx_file_id`.
- Filtered and paginated retrieval of TUs.
- Specialized methods for language operations (removing variants, updating codes)
  and content manipulation (clearing variants, deleting untranslated/duplicate TUs).
- Serialization/deserialization of complex metadata fields (properties, notes,
  custom attributes) stored as JSON in the database.
- Helper functions for robust ISO 8601 datetime string conversions.
"""
import sqlite3
import json
import re # For REGEXP function
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict, Tuple, Iterable

from .models import TMXHeader, TranslationUnit, TranslationUnitVariant, TMXProperty, TMXNote, TMXAttribute
from .exceptions import DatabaseError # Import custom exception

# Helper functions for datetime conversion
def dt_to_iso(dt_obj: Optional[datetime]) -> Optional[str]:
    """Converts a datetime object to an ISO 8601 string with Z for UTC."""
    if dt_obj is None:
        return None
    if dt_obj.tzinfo is None or dt_obj.tzinfo.utcoffset(dt_obj) is None:
        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
    return dt_obj.isoformat().replace('+00:00', 'Z')

def iso_to_dt(iso_str: Optional[str]) -> Optional[datetime]:
    """Converts an ISO 8601 string (ending in Z or +00:00) to a datetime object."""
    if iso_str is None:
        return None
    if iso_str.endswith('Z'):
        iso_str = iso_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(iso_str)
    except ValueError:
        try:
            dt_obj = datetime.strptime(iso_str, '%Y-%m-%dT%H:%M:%S.%f')
            return dt_obj.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                dt_obj = datetime.strptime(iso_str, '%Y-%m-%dT%H:%M:%S')
                return dt_obj.replace(tzinfo=timezone.utc)
            except ValueError:
                print(f"Warning: Could not parse ISO string '{iso_str}' to datetime.")
                return None

class SQLiteStore:
    """
    Manages storage of TMX data in an SQLite database.

    This class provides an interface to an SQLite database for storing and retrieving
    TMX file data, including headers, translation units, and their variants.
    It handles database connection, schema creation, and data operations.
    Most operations are expected to be performed within the context of a specific
    TMX file, identified by `tmx_file_id`.

    Attributes:
        db_path (str): Path to the SQLite database file. Defaults to ":memory:" for an in-memory database.
        conn (Optional[sqlite3.Connection]): The SQLite connection object. None if connection failed or is closed.
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        Initializes the SQLiteStore, establishes a database connection, and creates the schema.

        Args:
            db_path (str): The path to the SQLite database file.
                           Defaults to ":memory:" for an in-memory database.

        Raises:
            DatabaseError: If there's an error connecting to the database or initializing the schema.
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        try:
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON;")
            
            def regexp(expr: str, item: str) -> bool:
                """Custom REGEXP function for SQLite."""
                if item is None: return False
                try:
                    reg = re.compile(expr)
                    return reg.search(item) is not None
                except re.error: # Catch regex compilation errors
                    return False
            self.conn.create_function("REGEXP", 2, regexp)

            self._create_schema()
        except sqlite3.Error as e:
            # print(f"SQLiteStore initialization error: {e}") # Developer comment, consider logging
            if self.conn:
                self.conn.close()
            raise DatabaseError(message="SQLiteStore initialization error", details=str(e)) from e

    def _execute_script(self, script: str) -> None:
        """Executes a multi-statement SQL script."""
        if not self.conn:
            raise sqlite3.OperationalError("Database connection is not open.")
        try:
            self.conn.executescript(script)
        except sqlite3.Error as e:
            raise DatabaseError(message="Error executing SQL script.", details=str(e)) from e

    def _create_schema(self) -> None:
        """Creates the database schema if tables do not already exist."""
        schema_script = """
        CREATE TABLE IF NOT EXISTS tmx_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,       -- Path to the original TMX file
            import_date TEXT NOT NULL,           -- ISO datetime of import into this DB system
            last_modified_db TEXT NOT NULL     -- ISO datetime of last modification in this DB system
        );

        CREATE TABLE IF NOT EXISTS tmx_header (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmx_file_id INTEGER NOT NULL UNIQUE, -- Foreign key to tmx_files table
            creation_tool TEXT, creation_tool_version TEXT, seg_type TEXT NOT NULL,
            o_tmf TEXT, admin_lang TEXT NOT NULL, src_lang TEXT NOT NULL,
            datatype TEXT NOT NULL, o_encoding TEXT, creation_date TEXT, -- ISO datetime
            creation_id TEXT, change_date TEXT, change_id TEXT,         -- ISO datetime
            properties TEXT, notes TEXT, custom_attributes TEXT,       -- JSON lists of objects
            FOREIGN KEY (tmx_file_id) REFERENCES tmx_files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS translation_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmx_file_id INTEGER NOT NULL,      -- Foreign key to tmx_files table
            tuid TEXT,                         -- Optional TMX attribute tuid
            usage_count INTEGER,
            last_usage_date TEXT,              -- ISO datetime
            creation_date TEXT, creation_id TEXT, -- ISO datetime
            change_date TEXT, change_id TEXT,   -- ISO datetime
            seg_type TEXT, datatype TEXT,       -- Optional TMX attributes
            srclang TEXT,                      -- Optional TMX attribute srclang (often same as header's src_lang)
            properties TEXT, notes TEXT, custom_attributes TEXT, -- JSON lists of objects
            original_position INTEGER,         -- Original order in the TMX file
            UNIQUE (tmx_file_id, tuid),        -- TUID should be unique within a file if present
            FOREIGN KEY (tmx_file_id) REFERENCES tmx_files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS translation_unit_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tu_id INTEGER NOT NULL,            -- Foreign key to translation_units table
            segment_xml TEXT NOT NULL,         -- Full XML content of the <seg>
            segment_text_pure TEXT,            -- Plain text content of the segment
            lang TEXT NOT NULL,                -- Language code (xml:lang)
            creation_date TEXT, creation_id TEXT, -- ISO datetime
            change_date TEXT, change_id TEXT,   -- ISO datetime
            last_usage_date TEXT,              -- ISO datetime
            usage_count INTEGER,
            properties TEXT, notes TEXT, custom_attributes TEXT, -- JSON lists of objects
            UNIQUE (tu_id, lang),              -- Each TU can only have one variant per language
            FOREIGN KEY (tu_id) REFERENCES translation_units(id) ON DELETE CASCADE
        );
        """
        if self.conn:
            self._execute_script(schema_script)
            self.conn.commit()

    def get_or_create_tmx_file_entry(self, filepath: str) -> int:
        """
        Retrieves the ID of an existing TMX file entry or creates a new one.
        Updates `last_modified_db` timestamp if entry exists.

        Args:
            filepath (str): The path to the TMX file.

        Returns:
            int: The database ID of the TMX file entry.

        Raises:
            DatabaseError: If database operations fail.
            sqlite3.Error: If fetching the new ID fails after insertion.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        now_iso = dt_to_iso(datetime.now(timezone.utc))
        try:
            with self.conn: # Transaction management
                cursor = self.conn.execute("SELECT id FROM tmx_files WHERE filepath = ?", (filepath,))
                row = cursor.fetchone()
                if row:
                    self.conn.execute("UPDATE tmx_files SET last_modified_db = ? WHERE id = ?", (now_iso, row["id"]))
                    return row["id"]
                else:
                    cursor = self.conn.execute("INSERT INTO tmx_files (filepath, import_date, last_modified_db) VALUES (?, ?, ?)", (filepath, now_iso, now_iso))
                    new_id = cursor.lastrowid
                    if new_id is None: raise sqlite3.Error("Failed to get lastrowid for tmx_files insertion.")
                    return new_id
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error in get_or_create_tmx_file_entry for '{filepath}'.", details=str(e)) from e

    def close(self) -> None:
        """Closes the database connection if it is open."""
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error as e:
                print(f"Error closing database connection for {self.db_path}: {e}") # Retain print for critical close ops
            finally:
                self.conn = None

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the runtime context related to this object, ensuring the connection is closed."""
        self.close()

    def clear_database(self) -> None:
        """
        Clears all data from TMX-related tables and resets their sequences.
        Useful for preparing for a new TMX file import session if the database
        is meant to hold only one active TMX file's data at a time.

        Raises:
            DatabaseError: If database operations fail.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn: # Transaction management
                self.conn.execute("DELETE FROM translation_unit_variants;")
                self.conn.execute("DELETE FROM translation_units;")
                self.conn.execute("DELETE FROM tmx_header;")
                self.conn.execute("DELETE FROM tmx_files;")
                # Reset auto-increment counters for these tables
                self.conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('translation_unit_variants', 'translation_units', 'tmx_header', 'tmx_files');")
        except sqlite3.Error as e:
            raise DatabaseError(message="Error clearing database.", details=str(e)) from e

    def prepare_new_tmx_file_session(self, filepath: str) -> int:
        """
        Prepares the database for a new TMX file session by clearing existing data
        and creating a new TMX file entry.

        Args:
            filepath (str): The path to the new TMX file.

        Returns:
            int: The database ID of the new TMX file entry.
        """
        self.clear_database() # Clears all tables
        return self.get_or_create_tmx_file_entry(filepath) # Creates a new tmx_files entry

    def save_header(self, header_model: TMXHeader, tmx_file_id: int) -> None:
        """
        Saves or updates the TMX header for a given `tmx_file_id`.
        If a header for `tmx_file_id` exists, it's updated; otherwise, it's inserted.

        Args:
            header_model (TMXHeader): The TMXHeader Pydantic model to save.
            tmx_file_id (int): The ID of the TMX file this header belongs to.

        Raises:
            DatabaseError: If database operations fail.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        # Prepare data dictionary from the Pydantic model
        data = header_model.model_dump(exclude={"properties", "notes", "custom_attributes"}) # Exclude fields stored as JSON
        data["tmx_file_id"] = tmx_file_id
        # Serialize complex fields to JSON strings
        data["properties"] = json.dumps([p.model_dump() for p in header_model.properties])
        data["notes"] = json.dumps([n.model_dump() for n in header_model.notes])
        data["custom_attributes"] = json.dumps([ca.model_dump() for ca in header_model.custom_attributes])
        # Convert datetime objects to ISO strings
        data["creation_date"] = dt_to_iso(header_model.creation_date)
        data["change_date"] = dt_to_iso(header_model.change_date)
        
        try:
            with self.conn: # Transaction management
                # Check if header for this tmx_file_id already exists
                if self.conn.execute("SELECT id FROM tmx_header WHERE tmx_file_id = ?", (tmx_file_id,)).fetchone():
                    # Update existing header
                    keys_to_update = ", ".join(f"{k} = :{k}" for k in data if k != "tmx_file_id") # Don't update PK
                    update_query = f"UPDATE tmx_header SET {keys_to_update} WHERE tmx_file_id = :tmx_file_id"
                    self.conn.execute(update_query, data)
                else:
                    # Insert new header
                    cols = ", ".join(data.keys())
                    placeholders = ", ".join(f":{k}" for k in data.keys())
                    insert_query = f"INSERT INTO tmx_header ({cols}) VALUES ({placeholders})"
                    self.conn.execute(insert_query, data)
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error saving header for tmx_file_id {tmx_file_id}.", details=str(e)) from e

    def get_header(self, tmx_file_id: int) -> Optional[TMXHeader]:
        """
        Retrieves the TMX header for a given `tmx_file_id`.

        Args:
            tmx_file_id (int): The ID of the TMX file.

        Returns:
            Optional[TMXHeader]: The TMXHeader Pydantic model if found, otherwise None.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        row = self.conn.execute("SELECT * FROM tmx_header WHERE tmx_file_id = ?", (tmx_file_id,)).fetchone()
        if row:
            data = dict(row)
            # Deserialize JSON fields back to Pydantic models or lists of them
            data["properties"] = [TMXProperty(**p) for p in json.loads(data.pop("properties", '[]') or '[]')]
            data["notes"] = [TMXNote(**n) for n in json.loads(data.pop("notes", '[]') or '[]')]
            data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(data.pop("custom_attributes", '[]') or '[]')]
            # Convert ISO datetime strings back to datetime objects
            for df in ["creation_date", "change_date"]: data[df] = iso_to_dt(data[df]) if data.get(df) else None
            
            data.pop("id", None) # Remove internal DB ID
            data.pop("tmx_file_id", None) # Remove foreign key before model instantiation
            return TMXHeader(**data)
        return None

    def add_translation_unit(self, tu: TranslationUnit, tmx_file_id: int, position: Optional[int] = None) -> int:
        """
        Adds a single TranslationUnit and its variants to the database.

        Args:
            tu (TranslationUnit): The TranslationUnit Pydantic model.
            tmx_file_id (int): The ID of the TMX file this TU belongs to.
            position (Optional[int]): The original position of the TU in the file.

        Returns:
            int: The database ID of the newly inserted translation unit.

        Raises:
            DatabaseError: If database operations fail.
            sqlite3.Error: If fetching the new TU ID fails.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        # Prepare TU data
        tu_data = tu.model_dump(exclude={"variants", "properties", "notes", "custom_attributes", "id_in_db"})
        tu_data.update({
            "tmx_file_id": tmx_file_id, 
            "original_position": position,
            "properties": json.dumps([p.model_dump() for p in tu.properties]),
            "notes": json.dumps([n.model_dump() for n in tu.notes]),
            "custom_attributes": json.dumps([ca.model_dump() for ca in tu.custom_attributes]),
            "last_usage_date": dt_to_iso(tu.last_usage_date), 
            "creation_date": dt_to_iso(tu.creation_date),
            "change_date": dt_to_iso(tu.change_date)
        })
        
        cols = ", ".join(tu_data.keys())
        placeholders = ", ".join(f":{k}" for k in tu_data.keys())
        insert_tu_query = f"INSERT INTO translation_units ({cols}) VALUES ({placeholders})"
        
        try:
            with self.conn: # Transaction for TU and its TUVs
                cursor = self.conn.execute(insert_tu_query, tu_data)
                tu_db_id = cursor.lastrowid
                if tu_db_id is None: raise sqlite3.Error("Failed to get lastrowid for TU insertion.")

                # Add variants
                for var in tu.variants:
                    var_data = var.model_dump(exclude={"properties", "notes", "custom_attributes"})
                    var_data.update({
                        "tu_id": tu_db_id,
                        "properties": json.dumps([p.model_dump() for p in var.properties]),
                        "notes": json.dumps([n.model_dump() for n in var.notes]),
                        "custom_attributes": json.dumps([ca.model_dump() for ca in var.custom_attributes]),
                        "creation_date": dt_to_iso(var.creation_date), 
                        "change_date": dt_to_iso(var.change_date),
                        "last_usage_date": dt_to_iso(var.last_usage_date)
                    })
                    cols_v = ", ".join(var_data.keys())
                    placeholders_v = ", ".join(f":{k}" for k in var_data.keys())
                    insert_var_query = f"INSERT INTO translation_unit_variants ({cols_v}) VALUES ({placeholders_v})"
                    self.conn.execute(insert_var_query, var_data)
                return tu_db_id
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error adding TU (tuid: {tu.tuid}) for tmx_file_id {tmx_file_id}.", details=str(e)) from e

    def add_translation_units(self, tus: Iterable[TranslationUnit], tmx_file_id: int) -> List[int]:
        """
        Adds multiple TranslationUnits and their variants in a single transaction.

        Args:
            tus (Iterable[TranslationUnit]): An iterable of TranslationUnit models.
            tmx_file_id (int): The ID of the TMX file these TUs belong to.

        Returns:
            List[int]: A list of database IDs for the newly inserted TUs.

        Raises:
            DatabaseError: If database operations fail.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        ids: List[int] = []
        try:
            with self.conn: # Single transaction for all TUs
                for i, tu_model in enumerate(tus):
                    # Pass `i` as original_position, assuming `tus` is ordered.
                    ids.append(self.add_translation_unit(tu_model, tmx_file_id, position=i))
            return ids
        except sqlite3.Error as e: # Catch errors from add_translation_unit or commit
            raise DatabaseError(message=f"Error adding multiple TUs for tmx_file_id {tmx_file_id}.", details=str(e)) from e

    def _row_to_tu(self, tu_row: sqlite3.Row) -> TranslationUnit:
        """
        Converts a database row from `translation_units` table to a TranslationUnit Pydantic model,
        including its variants.

        Args:
            tu_row (sqlite3.Row): The row object from the database.

        Returns:
            TranslationUnit: The populated TranslationUnit model.
        
        Raises:
            sqlite3.OperationalError: If the database connection is not open.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        tu_id = tu_row["id"] # This is the DB ID of the TU
        data = dict(tu_row) # Convert row to dict
        
        # Deserialize JSON fields
        data["properties"] = [TMXProperty(**p) for p in json.loads(data.pop("properties", '[]') or '[]')]
        data["notes"] = [TMXNote(**n) for n in json.loads(data.pop("notes", '[]') or '[]')]
        data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(data.pop("custom_attributes", '[]') or '[]')]
        
        # Convert datetime strings
        for df in ["last_usage_date", "creation_date", "change_date"]:
            data[df] = iso_to_dt(data[df]) if data.get(df) else None
        
        # Fetch and build variants
        variants: List[TranslationUnitVariant] = []
        variant_query = "SELECT * FROM translation_unit_variants WHERE tu_id = ? ORDER BY lang"
        for var_row in self.conn.execute(variant_query, (tu_id,)):
            var_data = dict(var_row)
            var_data["properties"] = [TMXProperty(**p) for p in json.loads(var_data.pop("properties", '[]') or '[]')]
            var_data["notes"] = [TMXNote(**n) for n in json.loads(var_data.pop("notes", '[]') or '[]')]
            var_data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(var_data.pop("custom_attributes", '[]') or '[]')]
            for df_v in ["creation_date", "change_date", "last_usage_date"]:
                var_data[df_v] = iso_to_dt(var_data[df_v]) if var_data.get(df_v) else None
            
            var_data.pop("id", None) # Remove TUV's internal DB ID
            var_data.pop("tu_id", None) # Remove foreign key
            variants.append(TranslationUnitVariant(**var_data))
        data["variants"] = variants
        
        # Map DB ID to id_in_db field in model, remove other DB-specific fields
        data["id_in_db"] = data.pop("id") 
        data.pop("tmx_file_id", None)
        data.pop("original_position", None) # This info is used for ordering but not part of the core TU model identity
        
        return TranslationUnit(**data)

    def get_translation_unit_by_db_id(self, tu_db_id: int) -> Optional[TranslationUnit]:
        """
        Retrieves a single TranslationUnit by its database ID.

        Args:
            tu_db_id (int): The database ID of the TranslationUnit.

        Returns:
            Optional[TranslationUnit]: The TranslationUnit model if found, else None.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        row = self.conn.execute("SELECT * FROM translation_units WHERE id = ?", (tu_db_id,)).fetchone()
        return self._row_to_tu(row) if row else None

    def get_translation_unit_by_tuid(self, tuid: str, tmx_file_id: int) -> Optional[TranslationUnit]:
        """
        Retrieves a single TranslationUnit by its TUID within a specific TMX file.

        Args:
            tuid (str): The TUID of the TranslationUnit.
            tmx_file_id (int): The ID of the TMX file.

        Returns:
            Optional[TranslationUnit]: The TranslationUnit model if found, else None.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        query = "SELECT * FROM translation_units WHERE tuid = ? AND tmx_file_id = ?"
        row = self.conn.execute(query, (tuid, tmx_file_id)).fetchone()
        return self._row_to_tu(row) if row else None

    def get_all_translation_units(self, tmx_file_id: int, offset: int = 0, limit: int = -1) -> List[TranslationUnit]:
        """
        Retrieves all TranslationUnits for a TMX file, with optional pagination.
        Ordered by original position and then by database ID.

        Args:
            tmx_file_id (int): The ID of the TMX file.
            offset (int): The number of TUs to skip (for pagination).
            limit (int): The maximum number of TUs to return (-1 for no limit).

        Returns:
            List[TranslationUnit]: A list of TranslationUnit models.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        query_sql = "SELECT * FROM translation_units WHERE tmx_file_id = ? ORDER BY original_position, id"
        params_list: List[Any] = [tmx_file_id]
        
        if limit > -1: # SQLite uses -1 for no limit by default if LIMIT clause is absent
            query_sql += " LIMIT ? OFFSET ?"
            params_list.extend([limit, offset])
            
        return [self._row_to_tu(row) for row in self.conn.execute(query_sql, tuple(params_list))]

    def update_translation_unit(self, tu_db_id: int, tu: TranslationUnit) -> bool:
        """
        Updates an existing TranslationUnit and its variants.
        The entire TU and its variants are replaced based on the provided model.
        `change_date` of the TU is automatically set to the current time.

        Args:
            tu_db_id (int): The database ID of the TU to update.
            tu (TranslationUnit): The TranslationUnit model with updated data.

        Returns:
            bool: True if the update was successful, False otherwise (e.g., TU not found).
        
        Raises:
            DatabaseError: If a database error occurs during the transaction.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        # Prepare TU data for update
        tu_data = tu.model_dump(exclude={"variants", "properties", "notes", "custom_attributes", "id_in_db"})
        tu_data["id"] = tu_db_id # For WHERE clause
        tu_data["properties"] = json.dumps([p.model_dump() for p in tu.properties])
        tu_data["notes"] = json.dumps([n.model_dump() for n in tu.notes])
        tu_data["custom_attributes"] = json.dumps([ca.model_dump() for ca in tu.custom_attributes])
        tu_data["last_usage_date"] = dt_to_iso(tu.last_usage_date)
        tu_data["creation_date"] = dt_to_iso(tu.creation_date) # Preserve original creation date
        tu_data["change_date"] = dt_to_iso(datetime.now(timezone.utc)) # Set new change date
        
        keys_to_update = ", ".join(f"{k} = :{k}" for k in tu_data if k != "id") # Exclude 'id' from SET clause
        update_tu_sql = f"UPDATE translation_units SET {keys_to_update} WHERE id = :id"
        
        try:
            with self.conn: # Transaction
                cursor = self.conn.execute(update_tu_sql, tu_data)
                if cursor.rowcount == 0:
                    return False # TU with tu_db_id not found

                # Delete existing variants for this TU
                self.conn.execute("DELETE FROM translation_unit_variants WHERE tu_id = ?", (tu_db_id,))

                # Insert new variants from the provided TU model
                for var in tu.variants:
                    var_data = var.model_dump(exclude={"properties", "notes", "custom_attributes"})
                    var_data.update({
                        "tu_id": tu_db_id,
                        "properties": json.dumps([p.model_dump() for p in var.properties]),
                        "notes": json.dumps([n.model_dump() for n in var.notes]),
                        "custom_attributes": json.dumps([ca.model_dump() for ca in var.custom_attributes]),
                        "creation_date": dt_to_iso(var.creation_date), 
                        "change_date": dt_to_iso(var.change_date or datetime.now(timezone.utc)), # Use provided or set new
                        "last_usage_date": dt_to_iso(var.last_usage_date)
                    })
                    cols_v = ", ".join(var_data.keys())
                    placeholders_v = ", ".join(f":{k}" for k in var_data.keys())
                    insert_var_sql = f"INSERT INTO translation_unit_variants ({cols_v}) VALUES ({placeholders_v})"
                    self.conn.execute(insert_var_sql, var_data)
            return True
        except sqlite3.Error as e:
            # print(f"Error updating TU (db_id: {tu_db_id}): {e}") # Developer comment
            raise DatabaseError(message=f"Error updating TU (db_id: {tu_db_id}).", details=str(e)) from e


    def delete_translation_unit(self, tu_db_id: int) -> bool:
        """
        Deletes a TranslationUnit and its associated variants (due to ON DELETE CASCADE).

        Args:
            tu_db_id (int): The database ID of the TU to delete.

        Returns:
            bool: True if the TU was deleted, False otherwise (e.g., not found).
        
        Raises:
            DatabaseError: If a database error occurs.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn: # Transaction
                cursor = self.conn.execute("DELETE FROM translation_units WHERE id = ?", (tu_db_id,))
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            # print(f"Error deleting TU (db_id: {tu_db_id}): {e}") # Developer comment
            raise DatabaseError(message=f"Error deleting TU (db_id: {tu_db_id}).", details=str(e)) from e

    def get_language_codes(self, tmx_file_id: Optional[int] = None) -> List[str]:
        """
        Retrieves a sorted list of unique language codes present in TUVs.
        Can be scoped to a specific TMX file or across all TMX files.

        Args:
            tmx_file_id (Optional[int]): If provided, scope results to this TMX file.

        Returns:
            List[str]: Sorted list of unique language codes.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        if tmx_file_id is not None:
            query = """
                SELECT DISTINCT tuv.lang FROM translation_unit_variants tuv
                JOIN translation_units tu ON tuv.tu_id = tu.id
                WHERE tu.tmx_file_id = ? ORDER BY tuv.lang
            """
            return [r["lang"] for r in self.conn.execute(query, (tmx_file_id,))]
        else:
            query = "SELECT DISTINCT lang FROM translation_unit_variants ORDER BY lang"
            return [r["lang"] for r in self.conn.execute(query)]

    def get_tu_count(self, tmx_file_id: Optional[int] = None) -> int:
        """
        Gets the total count of TranslationUnits.
        Can be scoped to a specific TMX file.

        Args:
            tmx_file_id (Optional[int]): If provided, count TUs only for this TMX file.

        Returns:
            int: The total number of TUs.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        query_sql = "SELECT COUNT(*) FROM translation_units"
        params_list = []
        if tmx_file_id is not None:
            query_sql += " WHERE tmx_file_id = ?"
            params_list.append(tmx_file_id)
        
        row = self.conn.execute(query_sql, params_list).fetchone()
        return row[0] if row else 0

    def update_tuv_segment(self, tu_db_id: int, lang: str, new_segment_xml: str, new_segment_text_pure: Optional[str], change_id: Optional[str] = None) -> bool:
        """
        Updates the segment XML and pure text content of a specific TUV.
        Also updates its `change_date` and `change_id`.

        Args:
            tu_db_id (int): The database ID of the parent TU.
            lang (str): The language code of the TUV.
            new_segment_xml (str): The new XML content for the segment.
            new_segment_text_pure (Optional[str]): The new plain text content.
            change_id (Optional[str]): Identifier for the change.

        Returns:
            bool: True if the update was successful, False otherwise (e.g., TUV not found).

        Raises:
            DatabaseError: If a database error occurs.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn: # Transaction
                query_sql = """
                    UPDATE translation_unit_variants 
                    SET segment_xml = ?, segment_text_pure = ?, change_date = ?, change_id = ? 
                    WHERE tu_id = ? AND lang = ?
                """
                params = (
                    new_segment_xml, new_segment_text_pure, 
                    dt_to_iso(datetime.now(timezone.utc)), change_id, 
                    tu_db_id, lang
                )
                return self.conn.execute(query_sql, params).rowcount > 0
        except sqlite3.Error as e:
            # print(f"Error updating TUV segment ({tu_db_id}, {lang}): {e}") # Developer comment
            raise DatabaseError(message=f"Error updating TUV segment (TU ID: {tu_db_id}, Lang: {lang}).", details=str(e)) from e

    def update_tuv_field(self, tu_db_id: int, lang: str, field_name: str, field_value: Any, change_id: Optional[str] = None) -> bool:
        """
        Updates a specific metadata field (properties, notes, custom_attributes) of a TUV.
        The `field_value` is serialized to JSON. `change_date` and `change_id` are also updated.

        Args:
            tu_db_id (int): Database ID of the parent TU.
            lang (str): Language code of the TUV.
            field_name (str): Name of the metadata field to update.
            field_value (Any): The new value for the field (should be JSON serializable).
            change_id (Optional[str]): Identifier for the change.

        Returns:
            bool: True if successful, False otherwise.

        Raises:
            DatabaseError: If a database or JSON serialization error occurs.
            ValueError: If `field_name` is invalid.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        if field_name not in {"properties", "notes", "custom_attributes"}:
            raise ValueError(f"Invalid TUV field for update: {field_name}")
        
        try:
            json_val = json.dumps(field_value) # Serialize the value to JSON
            current_time_iso = dt_to_iso(datetime.now(timezone.utc))
            with self.conn: # Transaction
                query_sql = f"""
                    UPDATE translation_unit_variants 
                    SET {field_name} = ?, change_date = ?, change_id = ? 
                    WHERE tu_id = ? AND lang = ?
                """
                params = (json_val, current_time_iso, change_id, tu_db_id, lang)
                return self.conn.execute(query_sql, params).rowcount > 0
        except (sqlite3.Error, TypeError, json.JSONDecodeError) as e: # Catch DB and JSON errors
            # print(f"Error updating TUV field ({tu_db_id}, {lang}, {field_name}): {e}") # Developer comment
            raise DatabaseError(message=f"Error updating TUV field (TU ID: {tu_db_id}, Lang: {lang}, Field: {field_name}).", details=str(e)) from e

    def get_translation_units_filtered(
        self,
        tmx_file_id: int,
        filter_text: Optional[str] = None,
        filter_lang_code: Optional[str] = None, # Target language for text filter
        case_sensitive_filter: bool = False,
        is_regex_filter: bool = False,
        filter_untranslated_to_other_langs: bool = False,
        source_lang_for_untranslated_filter: Optional[str] = None,
        # target_langs_for_untranslated_filter: Optional[List[str]] = None, # Deferred
        sort_by_text_in_lang_code: Optional[str] = None, # Language to sort by segment text
        sort_ascending: bool = True,
        start_offset: int = 0,
        page_size: int = 20 
    ) -> Tuple[List[TranslationUnit], int]:
        """
        Retrieves TranslationUnits with extensive filtering, sorting, and pagination.

        Args:
            tmx_file_id: ID of the TMX file to search within.
            filter_text: Text to search for in segments.
            filter_lang_code: Language code for `filter_text`. If None, behavior might be undefined or error.
            case_sensitive_filter: Whether `filter_text` search is case-sensitive.
            is_regex_filter: Whether `filter_text` is a regular expression.
            filter_untranslated_to_other_langs: If True, find TUs with non-empty source but missing/empty targets
                                                in languages other than `source_lang_for_untranslated_filter`.
            source_lang_for_untranslated_filter: Source language for the 'untranslated' filter.
            sort_by_text_in_lang_code: Language code whose segment text to sort by.
            sort_ascending: Sort order (True for ASC, False for DESC).
            start_offset: Offset for pagination.
            page_size: Number of TUs per page. Use 0 or negative for no limit (fetches all).

        Returns:
            Tuple[List[TranslationUnit], int]: A list of matching TranslationUnit models and the
                                               total count of TUs matching filters (before pagination).
        
        Raises:
            DatabaseError: If a database error occurs.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")

        params_list: List[Any] = [] # Renamed from 'params' to avoid conflict if method signature changes
        
        # Base queries
        base_select_tu_cols = "SELECT DISTINCT tu.id AS tu_main_id, tu.* FROM translation_units tu"
        count_select = "SELECT COUNT(DISTINCT tu.id) FROM translation_units tu"
        
        joins_list: List[str] = [] # Renamed from 'joins'
        where_clauses_list: List[str] = ["tu.tmx_file_id = ?"] # Renamed from 'where_clauses'
        params_list.append(tmx_file_id)

        # Text filter logic
        if filter_text and filter_lang_code:
            joins_list.append(f"JOIN translation_unit_variants tuv_filter ON tu.id = tuv_filter.tu_id AND tuv_filter.lang = ?")
            params_list.append(filter_lang_code)
            
            text_column_expr = "tuv_filter.segment_text_pure"
            filter_operator = "REGEXP" if is_regex_filter else "LIKE"
            actual_filter_text = filter_text
            
            if not is_regex_filter and not case_sensitive_filter:
                text_column_expr = f"LOWER({text_column_expr})"
                actual_filter_text = actual_filter_text.lower()
            
            if not is_regex_filter: # Add wildcards for LIKE search
                 actual_filter_text = f"%{actual_filter_text}%"

            where_clauses_list.append(f"{text_column_expr} {filter_operator} ?")
            params_list.append(actual_filter_text)

        # Untranslated filter logic
        if filter_untranslated_to_other_langs and source_lang_for_untranslated_filter:
            # Join for the source variant to ensure it exists and is non-empty
            joins_list.append(f"JOIN translation_unit_variants tuv_src_untrans ON tu.id = tuv_src_untrans.tu_id AND tuv_src_untrans.lang = ?")
            params_list.append(source_lang_for_untranslated_filter)
            where_clauses_list.append("(tuv_src_untrans.segment_text_pure IS NOT NULL AND tuv_src_untrans.segment_text_pure != '')")
            
            # Subquery to ensure NO other language variant is properly translated
            where_clauses_list.append(f"""
                NOT EXISTS (
                    SELECT 1 FROM translation_unit_variants tuv_target_check
                    WHERE tuv_target_check.tu_id = tu.id
                    AND tuv_target_check.lang != ? 
                    AND (tuv_target_check.segment_text_pure IS NOT NULL AND tuv_target_check.segment_text_pure != '')
                )
            """)
            params_list.append(source_lang_for_untranslated_filter)


        # Construct WHERE and JOIN clauses for count query first
        current_join_str = " ".join(list(set(joins_list))) # Use set to avoid duplicates from different filter conditions
        current_where_str = " AND ".join(where_clauses_list) if where_clauses_list else "1=1"

        full_count_query = f"{count_select} {current_join_str} WHERE {current_where_str}"
        total_matching_count = self.conn.execute(full_count_query, tuple(params_list)).fetchone()[0] or 0
        
        # Data query: Order by and Pagination
        order_by_clauses_list: List[str] = [] # Renamed
        # Parameters for sorting might be added, so create a copy for data query params
        data_query_params = list(params_list) 

        if sort_by_text_in_lang_code:
            sort_join_alias = "tuv_sort_alias" # Unique alias for sort join
            # Check if a compatible join already exists to avoid redundant joins (simplified check)
            if not any(f"tuv_filter.lang = ?" in j and filter_lang_code == sort_by_text_in_lang_code for j in joins_list if "tuv_filter" in j):
                 joins_list.append(f"LEFT JOIN translation_unit_variants {sort_join_alias} ON tu.id = {sort_join_alias}.tu_id AND {sort_join_alias}.lang = ?")
                 data_query_params.append(sort_by_text_in_lang_code)
            else: # If filter join can be reused for sorting
                sort_join_alias = "tuv_filter" 

            collate_str = "COLLATE NOCASE" if not case_sensitive_filter else ""
            order_by_clauses_list.append(f"{sort_join_alias}.segment_text_pure {collate_str} {'ASC' if sort_ascending else 'DESC'}")
        
        order_by_clauses_list.append("tu.original_position ASC")
        order_by_clauses_list.append("tu.id ASC") # Final tie-breaker

        current_order_by_str = "ORDER BY " + ", ".join(order_by_clauses_list)
        
        # Re-evaluate join_str for data query if sort added a new join specifically
        current_join_str_for_data = " ".join(list(set(joins_list))) 

        pagination_str = ""
        if page_size > 0:
            pagination_str = "LIMIT ? OFFSET ?"
            data_query_params.append(page_size)
            data_query_params.append(start_offset)

        full_data_query = f"{base_select_tu_cols} {current_join_str_for_data} WHERE {current_where_str} {current_order_by_str} {pagination_str}"
        
        tu_rows = self.conn.execute(full_data_query, tuple(data_query_params)).fetchall()
        
        tus_model_list = [self._row_to_tu(row) for row in tu_rows]
        
        return tus_model_list, total_matching_count


# Example Usage (Mainly for basic schema and connection testing after modifications)
if __name__ == '__main__':
    print("Testing SQLiteStore with in-memory database (Chunk 4 updates)...")
    # This needs more comprehensive setup to test get_translation_units_filtered
    # For now, just ensuring the class can be instantiated with the new method.
    try:
        with SQLiteStore(db_path=":memory:") as store:
            print("Schema created, REGEXP function added.")
            # Example: file_id = store.get_or_create_tmx_file_entry("test_filter.tmx")
            # ... (populate with some TUs and TUVs) ...
            # results, count = store.get_translation_units_filtered(tmx_file_id=file_id, filter_text="example", filter_lang_code="en")
            # print(f"Found {count} units, got {len(results)} for page.")
            print("SQLiteStore with get_translation_units_filtered initialized (not fully tested here).")
            
    except sqlite3.Error as e:
        print(f"SQLiteStore Chunk 4 test error: {e}")
    except AssertionError as e:
        print(f"AssertionError in Chunk 4 tests: {e}")
    finally:
        print("In-memory Chunk 4 test finished.")

    # --- Methods for Chunk 5 Part 1: Language Operations ---

    def remove_language_variants(self, lang_code: str, tmx_file_id: int) -> int:
        """
        Deletes all Translation Unit Variants (TUVs) for a specific language code
        within a given TMX file.

        Args:
            lang_code (str): The language code of the variants to delete (e.g., "fr-FR").
            tmx_file_id (int): The database ID of the TMX file from which to delete variants.

        Returns:
            int: The number of TUVs deleted.

        Raises:
            sqlite3.OperationalError: If the database connection is not open.
            DatabaseError: If any other SQLite error occurs during the operation.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn: # Transaction management
                sql = """
                    DELETE FROM translation_unit_variants
                    WHERE lang = ? 
                    AND tu_id IN (SELECT id FROM translation_units WHERE tmx_file_id = ?)
                """
                cursor = self.conn.execute(sql, (lang_code, tmx_file_id))
                return cursor.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error removing language variants for lang '{lang_code}', tmx_file_id {tmx_file_id}.", details=str(e)) from e

    def update_language_code_in_variants(
        self, old_lang_code: str, new_lang_code: str, tmx_file_id: int, current_user_id: Optional[str] = None
    ) -> int:
        """
        Updates the language code of TUVs from an old code to a new code for a specific TMX file.
        It also updates the `change_date` and `change_id` for the modified TUVs.

        Args:
            old_lang_code (str): The current language code of the TUVs to be updated.
            new_lang_code (str): The new language code to set.
            tmx_file_id (int): The ID of the TMX file whose TUVs are to be updated.
            current_user_id (Optional[str]): Identifier for the user/process making the change.

        Returns:
            int: The number of TUVs updated.

        Raises:
            sqlite3.OperationalError: If the database connection is not open.
            sqlite3.IntegrityError: If the update violates a UNIQUE constraint (e.g., a TUV with
                                    the new language code already exists for a given TU).
                                    This is re-raised to be handled by the service layer.
            DatabaseError: For other SQLite errors.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        current_time_iso = dt_to_iso(datetime.now(timezone.utc))
        
        try:
            with self.conn: # Transaction management
                # Note: The service layer should ideally check for potential conflicts before calling this,
                # e.g., if new_lang_code would create a duplicate (tu_id, lang) pair.
                # This method attempts the update; SQLite will raise IntegrityError on conflict.
                sql = """
                    UPDATE translation_unit_variants
                    SET lang = ?, change_date = ?, change_id = ?
                    WHERE lang = ?
                    AND tu_id IN (SELECT id FROM translation_units WHERE tmx_file_id = ?)
                """
                params = (new_lang_code, current_time_iso, current_user_id, old_lang_code, tmx_file_id)
                cursor = self.conn.execute(sql, params)
                return cursor.rowcount
        except sqlite3.IntegrityError: # Re-raise to be specifically handled by service layer
            raise 
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error updating language code from '{old_lang_code}' to '{new_lang_code}' for tmx_file_id {tmx_file_id}.", details=str(e)) from e

    # --- Methods for Chunk 5 Part 2: Content Manipulation ---

    def get_tuv(self, tu_db_id: int, lang_code: str) -> Optional[TranslationUnitVariant]:
        """
        Fetches a specific TranslationUnitVariant by its parent TU's database ID and language code.

        Args:
            tu_db_id (int): The database ID of the parent Translation Unit.
            lang_code (str): The language code of the TUV to retrieve.

        Returns:
            Optional[TranslationUnitVariant]: The TUV Pydantic model if found, else None.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        sql = "SELECT * FROM translation_unit_variants WHERE tu_id = ? AND lang = ?"
        row = self.conn.execute(sql, (tu_db_id, lang_code)).fetchone()
        
        if row:
            var_data = dict(row)
            var_data["properties"] = [TMXProperty(**p) for p in json.loads(var_data.pop("properties", '[]') or '[]')]
            var_data["notes"] = [TMXNote(**n) for n in json.loads(var_data.pop("notes", '[]') or '[]')]
            var_data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(var_data.pop("custom_attributes", '[]') or '[]')]
            
            for df_v in ["creation_date", "change_date", "last_usage_date"]:
                 var_data[df_v] = iso_to_dt(var_data[df_v]) if var_data.get(df_v) else None
            
            var_data.pop("id", None) 
            var_data.pop("tu_id", None)
            return TranslationUnitVariant(**var_data)
        return None

    def save_tuv(self, tuv: TranslationUnitVariant, tu_db_id: int, current_user_id: Optional[str] = None) -> None:
        """
        Inserts or updates a single TranslationUnitVariant.
        If a TUV for the given `tu_db_id` and `lang` already exists, it's updated.
        Otherwise, a new TUV is inserted.
        `creation_date` and `creation_id` are set only on initial insertion if not provided.
        `change_date` and `change_id` are always updated.

        Args:
            tuv (TranslationUnitVariant): The TUV Pydantic model to save.
            tu_db_id (int): The database ID of the parent TU.
            current_user_id (Optional[str]): Identifier for the user/process making the change.

        Raises:
            DatabaseError: If a database error occurs.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")

        existing_tuv_row_id: Optional[int] = None
        cursor = self.conn.execute("SELECT id FROM translation_unit_variants WHERE tu_id = ? AND lang = ?", (tu_db_id, tuv.lang))
        row = cursor.fetchone()
        if row:
            existing_tuv_row_id = row["id"]

        now_iso = dt_to_iso(datetime.now(timezone.utc))
        
        tuv_data = tuv.model_dump(exclude={"properties", "notes", "custom_attributes", "creation_date", "creation_id"})
        tuv_data.update({
            "tu_id": tu_db_id,
            "change_date": now_iso,
            "change_id": current_user_id,
            "properties": json.dumps([p.model_dump() for p in tuv.properties]),
            "notes": json.dumps([n.model_dump() for n in tuv.notes]),
            "custom_attributes": json.dumps([ca.model_dump() for ca in tuv.custom_attributes]),
            "last_usage_date": dt_to_iso(tuv.last_usage_date)
        })

        try:
            with self.conn: # Transaction management
                if existing_tuv_row_id is not None: # Update existing TUV
                    tuv_data["id"] = existing_tuv_row_id 
                    update_fields = {k: v for k, v in tuv_data.items() if k not in ["id", "tu_id", "lang", "creation_date", "creation_id"]}
                    set_clauses = ", ".join([f"{key} = :{key}" for key in update_fields.keys()])
                    update_sql = f"UPDATE translation_unit_variants SET {set_clauses} WHERE id = :id"
                    self.conn.execute(update_sql, {**update_fields, "id": existing_tuv_row_id})
                else: # Insert new TUV
                    tuv_data["creation_date"] = dt_to_iso(tuv.creation_date) if tuv.creation_date else now_iso
                    tuv_data["creation_id"] = tuv.creation_id
                    
                    cols = ", ".join(tuv_data.keys())
                    placeholders = ", ".join(f":{k}" for k in tuv_data.keys())
                    insert_sql = f"INSERT INTO translation_unit_variants ({cols}) VALUES ({placeholders})"
                    self.conn.execute(insert_sql, tuv_data)
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error saving TUV for tu_db_id {tu_db_id}, lang {tuv.lang}.", details=str(e)) from e

    def delete_untranslated_tus(self, source_lang_code: str, tmx_file_id: int) -> int:
        """
        Deletes Translation Units (TUs) that meet specific "untranslated" criteria:
        1. The TU belongs to the specified `tmx_file_id`.
        2. It has a variant in the `source_lang_code` with non-empty pure text content.
        3. It has NO OTHER variants (in languages other than `source_lang_code`) that
           also have non-empty pure text content.
        
        Essentially, this removes TUs that are translated only in the source language
        but not effectively translated into any other target language.
        Deletion cascades to associated TUVs.

        Args:
            source_lang_code (str): The source language to check against.
            tmx_file_id (int): The ID of the TMX file to operate on.

        Returns:
            int: The number of TUs deleted.

        Raises:
            DatabaseError: If a database error occurs.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        sql_select_ids_to_delete = """
            SELECT tu.id
            FROM translation_units tu
            JOIN translation_unit_variants src_tuv ON tu.id = src_tuv.tu_id 
            WHERE tu.tmx_file_id = ? 
              AND src_tuv.lang = ?
              AND (src_tuv.segment_text_pure IS NOT NULL AND src_tuv.segment_text_pure != '') 
              AND NOT EXISTS ( /* Check that no other (target) variant is translated */
                  SELECT 1
                  FROM translation_unit_variants other_tuv
                  WHERE other_tuv.tu_id = tu.id
                    AND other_tuv.lang != ? /* Different from source language */
                    AND (other_tuv.segment_text_pure IS NOT NULL AND other_tuv.segment_text_pure != '')
              );
        """
        deleted_count = 0
        try:
            with self.conn: # Transaction
                params_select = (tmx_file_id, source_lang_code, source_lang_code)
                tu_ids_to_delete = [row["id"] for row in self.conn.execute(sql_select_ids_to_delete, params_select)]
                
                if not tu_ids_to_delete:
                    return 0
                
                placeholders = ', '.join('?' for _ in tu_ids_to_delete)
                delete_tu_sql = f"DELETE FROM translation_units WHERE id IN ({placeholders})"
                cursor = self.conn.execute(delete_tu_sql, tu_ids_to_delete)
                deleted_count = cursor.rowcount
            return deleted_count
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error deleting untranslated TUs for tmx_file_id {tmx_file_id}, source_lang {source_lang_code}.", details=str(e)) from e

    def clear_variants_matching_source(self, source_lang_code: str, tmx_file_id: int, current_user_id: Optional[str]) -> int:
        """
        Clears target language TUVs if their pure text content is identical to the
        source language TUV's pure text content within the same Translation Unit.
        "Clearing" involves setting `segment_xml` to an empty segment and `segment_text_pure`
        to an empty string, and updating `change_date` and `change_id`.

        Args:
            source_lang_code (str): The source language to compare against.
            tmx_file_id (int): The ID of the TMX file to operate on.
            current_user_id (Optional[str]): Identifier for the user/process making the change.

        Returns:
            int: The number of target TUVs cleared.

        Raises:
            DatabaseError: If a database error occurs during the operation.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        all_tus = self.get_all_translation_units(tmx_file_id=tmx_file_id) # Fetches full TU objects with variants
        cleared_count = 0
        
        empty_segment_xml = "<seg></seg>" 
        now_iso = dt_to_iso(datetime.now(timezone.utc))

        try:
            with self.conn: # Transaction for all updates
                for tu in all_tus:
                    if tu.id_in_db is None: continue # Should not happen with TUs from DB

                    source_tuv: Optional[TranslationUnitVariant] = None
                    for variant in tu.variants:
                        if variant.lang == source_lang_code:
                            source_tuv = variant
                            break
                    
                    # Proceed only if source TUV exists and has non-empty pure text
                    if source_tuv and source_tuv.segment_text_pure:
                        for target_tuv in tu.variants:
                            if target_tuv.lang != source_lang_code and \
                               target_tuv.segment_text_pure == source_tuv.segment_text_pure:
                                
                                update_sql = """
                                    UPDATE translation_unit_variants
                                    SET segment_xml = ?, segment_text_pure = ?, change_date = ?, change_id = ?
                                    WHERE tu_id = ? AND lang = ?
                                """
                                params_update = (
                                    empty_segment_xml, "", now_iso, current_user_id, 
                                    tu.id_in_db, target_tuv.lang
                                )
                                cursor = self.conn.execute(update_sql, params_update)
                                cleared_count += cursor.rowcount
            return cleared_count
        except sqlite3.Error as e:
            raise DatabaseError(message=f"Error clearing variants matching source for tmx_file_id {tmx_file_id}.", details=str(e)) from e

    def delete_duplicate_tus(self, tmx_file_id: int) -> int:
        """
        Deletes fully duplicate Translation Units (TUs) within a specific TMX file.
        A TU is considered a duplicate if it has the exact same set of variants (language code
        and pure segment text) as another TU. The TU with the lower `original_position`
        (or `id` as a tie-breaker) is kept.

        Args:
            tmx_file_id (int): The ID of the TMX file to operate on.

        Returns:
            int: The number of duplicate TUs deleted.

        Raises:
            DatabaseError: If a database error occurs.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")

        # Fetch all TUs for the file, ordered to ensure consistent "keep" choice
        all_tus = self.get_all_translation_units(tmx_file_id=tmx_file_id, limit=-1) 
        
        seen_signatures: Dict[str, int] = {} # Maps variant signature to the db_id of the TU to keep
        ids_to_delete: List[int] = []
        
        for tu in all_tus:
            if tu.id_in_db is None: continue 

            # Create a canonical signature for the TU's variants
            variant_details = []
            for var in sorted(tu.variants, key=lambda v: v.lang): # Sort by lang for consistency
                variant_details.append((var.lang, var.segment_text_pure or ""))
            signature = json.dumps(variant_details) # JSON dump of sorted list of tuples

            if signature in seen_signatures:
                # This TU is a duplicate of the one whose ID is stored in seen_signatures
                ids_to_delete.append(tu.id_in_db)
            else:
                # First time seeing this signature, mark this TU's ID as the one to keep
                seen_signatures[signature] = tu.id_in_db
        
        deleted_count = 0
        if ids_to_delete:
            try:
                with self.conn: # Transaction for deletion
                    placeholders = ', '.join('?' for _ in ids_to_delete)
                    delete_sql = f"DELETE FROM translation_units WHERE id IN ({placeholders})"
                    cursor = self.conn.execute(delete_sql, ids_to_delete)
                    deleted_count = cursor.rowcount
            except sqlite3.Error as e:
                raise DatabaseError(message=f"Error deleting duplicate TUs for tmx_file_id {tmx_file_id}.", details=str(e)) from e
        
        return deleted_count
