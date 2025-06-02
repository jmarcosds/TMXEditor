import sqlite3
import json
import re # For REGEXP function
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict, Tuple, Iterable

from .models import TMXHeader, TranslationUnit, TranslationUnitVariant, TMXProperty, TMXNote, TMXAttribute

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
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        try:
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON;")
            # Add REGEXP function for SQLite if not already present
            def regexp(expr, item):
                if item is None: # Should not happen if column is NOT NULL
                    return False
                reg = re.compile(expr)
                return reg.search(item) is not None
            self.conn.create_function("REGEXP", 2, regexp)

            self._create_schema()
        except sqlite3.Error as e:
            print(f"SQLiteStore initialization error: {e}")
            if self.conn:
                self.conn.close()
            raise

    def _execute_script(self, script: str) -> None:
        if not self.conn:
            raise sqlite3.OperationalError("Database connection is not open.")
        try:
            self.conn.executescript(script)
        except sqlite3.Error as e:
            print(f"Error executing SQL script: {e}")
            raise

    def _create_schema(self) -> None:
        schema_script = """
        CREATE TABLE IF NOT EXISTS tmx_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            import_date TEXT NOT NULL, 
            last_modified_db TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tmx_header (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmx_file_id INTEGER NOT NULL UNIQUE,
            creation_tool TEXT, creation_tool_version TEXT, seg_type TEXT NOT NULL,
            o_tmf TEXT, admin_lang TEXT NOT NULL, src_lang TEXT NOT NULL,
            datatype TEXT NOT NULL, o_encoding TEXT, creation_date TEXT,
            creation_id TEXT, change_date TEXT, change_id TEXT,
            properties TEXT, notes TEXT, custom_attributes TEXT,
            FOREIGN KEY (tmx_file_id) REFERENCES tmx_files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS translation_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmx_file_id INTEGER NOT NULL, tuid TEXT, usage_count INTEGER,
            last_usage_date TEXT, creation_date TEXT, creation_id TEXT,
            change_date TEXT, change_id TEXT, seg_type TEXT, datatype TEXT,
            srclang TEXT, properties TEXT, notes TEXT, custom_attributes TEXT,
            original_position INTEGER,
            UNIQUE (tmx_file_id, tuid),
            FOREIGN KEY (tmx_file_id) REFERENCES tmx_files(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS translation_unit_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tu_id INTEGER NOT NULL, segment_xml TEXT NOT NULL, segment_text_pure TEXT,
            lang TEXT NOT NULL, creation_date TEXT, creation_id TEXT,
            change_date TEXT, change_id TEXT, last_usage_date TEXT,
            usage_count INTEGER, properties TEXT, notes TEXT, custom_attributes TEXT,
            UNIQUE (tu_id, lang),
            FOREIGN KEY (tu_id) REFERENCES translation_units(id) ON DELETE CASCADE
        );
        """
        if self.conn:
            self._execute_script(schema_script)
            self.conn.commit()

    def get_or_create_tmx_file_entry(self, filepath: str) -> int:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        now_iso = dt_to_iso(datetime.now(timezone.utc))
        try:
            with self.conn:
                cursor = self.conn.execute("SELECT id FROM tmx_files WHERE filepath = ?", (filepath,))
                row = cursor.fetchone()
                if row:
                    self.conn.execute("UPDATE tmx_files SET last_modified_db = ? WHERE id = ?", (now_iso, row["id"]))
                    return row["id"]
                else:
                    cursor = self.conn.execute("INSERT INTO tmx_files (filepath, import_date, last_modified_db) VALUES (?, ?, ?)", (filepath, now_iso, now_iso))
                    new_id = cursor.lastrowid
                    if new_id is None: raise sqlite3.Error("Failed to get ID for tmx_files.")
                    return new_id
        except sqlite3.Error as e:
            print(f"Error in get_or_create_tmx_file_entry for {filepath}: {e}")
            raise

    def close(self) -> None:
        if self.conn:
            try: self.conn.close()
            except sqlite3.Error as e: print(f"Error closing DB: {e}")
            finally: self.conn = None

    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): self.close()

    def clear_database(self) -> None:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn:
                self.conn.execute("DELETE FROM translation_unit_variants;")
                self.conn.execute("DELETE FROM translation_units;")
                self.conn.execute("DELETE FROM tmx_header;")
                self.conn.execute("DELETE FROM tmx_files;")
                self.conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('translation_unit_variants', 'translation_units', 'tmx_header', 'tmx_files');")
        except sqlite3.Error as e: print(f"Error clearing DB: {e}"); raise

    def prepare_new_tmx_file_session(self, filepath: str) -> int:
        self.clear_database()
        return self.get_or_create_tmx_file_entry(filepath)

    def save_header(self, header_model: TMXHeader, tmx_file_id: int) -> None:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        data = header_model.model_dump(exclude={"properties", "notes", "custom_attributes"})
        data.update({
            "tmx_file_id": tmx_file_id,
            "properties": json.dumps([p.model_dump() for p in header_model.properties]),
            "notes": json.dumps([n.model_dump() for n in header_model.notes]),
            "custom_attributes": json.dumps([ca.model_dump() for ca in header_model.custom_attributes]),
            "creation_date": dt_to_iso(header_model.creation_date),
            "change_date": dt_to_iso(header_model.change_date),
        })
        try:
            with self.conn:
                if self.conn.execute("SELECT id FROM tmx_header WHERE tmx_file_id = ?", (tmx_file_id,)).fetchone():
                    keys_to_update = ", ".join(f"{k} = :{k}" for k in data if k != "tmx_file_id")
                    self.conn.execute(f"UPDATE tmx_header SET {keys_to_update} WHERE tmx_file_id = :tmx_file_id", data)
                else:
                    cols = ", ".join(data.keys())
                    placeholders = ", ".join(f":{k}" for k in data.keys())
                    self.conn.execute(f"INSERT INTO tmx_header ({cols}) VALUES ({placeholders})", data)
        except sqlite3.Error as e: print(f"Error saving header for tmx_file_id {tmx_file_id}: {e}"); raise

    def get_header(self, tmx_file_id: int) -> Optional[TMXHeader]:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        row = self.conn.execute("SELECT * FROM tmx_header WHERE tmx_file_id = ?", (tmx_file_id,)).fetchone()
        if row:
            data = dict(row)
            data["properties"] = [TMXProperty(**p) for p in json.loads(data.pop("properties", '[]'))]
            data["notes"] = [TMXNote(**n) for n in json.loads(data.pop("notes", '[]'))]
            data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(data.pop("custom_attributes", '[]'))]
            for df in ["creation_date", "change_date"]: data[df] = iso_to_dt(data[df]) if data.get(df) else None
            data.pop("id", None); data.pop("tmx_file_id", None)
            return TMXHeader(**data)
        return None

    def add_translation_unit(self, tu: TranslationUnit, tmx_file_id: int, position: Optional[int] = None) -> int:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        tu_data = tu.model_dump(exclude={"variants", "properties", "notes", "custom_attributes"})
        tu_data.update({
            "tmx_file_id": tmx_file_id, "original_position": position,
            "properties": json.dumps([p.model_dump() for p in tu.properties]),
            "notes": json.dumps([n.model_dump() for n in tu.notes]),
            "custom_attributes": json.dumps([ca.model_dump() for ca in tu.custom_attributes]),
            "last_usage_date": dt_to_iso(tu.last_usage_date), "creation_date": dt_to_iso(tu.creation_date),
            "change_date": dt_to_iso(tu.change_date)
        })
        cols = ", ".join(tu_data.keys()); placeholders = ", ".join(f":{k}" for k in tu_data.keys())
        cursor = self.conn.execute(f"INSERT INTO translation_units ({cols}) VALUES ({placeholders})", tu_data)
        tu_db_id = cursor.lastrowid
        if tu_db_id is None: raise sqlite3.Error("Failed to get ID for TU.")

        for var in tu.variants:
            var_data = var.model_dump(exclude={"properties", "notes", "custom_attributes"})
            var_data.update({
                "tu_id": tu_db_id,
                "properties": json.dumps([p.model_dump() for p in var.properties]),
                "notes": json.dumps([n.model_dump() for n in var.notes]),
                "custom_attributes": json.dumps([ca.model_dump() for ca in var.custom_attributes]),
                "creation_date": dt_to_iso(var.creation_date), "change_date": dt_to_iso(var.change_date),
                "last_usage_date": dt_to_iso(var.last_usage_date)
            })
            cols_v = ", ".join(var_data.keys()); placeholders_v = ", ".join(f":{k}" for k in var_data.keys())
            self.conn.execute(f"INSERT INTO translation_unit_variants ({cols_v}) VALUES ({placeholders_v})", var_data)
        return tu_db_id

    def add_translation_units(self, tus: Iterable[TranslationUnit], tmx_file_id: int) -> List[int]:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        ids = []
        try:
            with self.conn:
                for i, tu in enumerate(tus): ids.append(self.add_translation_unit(tu, tmx_file_id, position=i))
            return ids
        except sqlite3.Error as e: print(f"Error adding TUs for tmx_file_id {tmx_file_id}: {e}"); raise

    def _row_to_tu(self, tu_row: sqlite3.Row) -> TranslationUnit:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        tu_id = tu_row["id"]; data = dict(tu_row)
        data["properties"] = [TMXProperty(**p) for p in json.loads(data.pop("properties", '[]'))]
        data["notes"] = [TMXNote(**n) for n in json.loads(data.pop("notes", '[]'))]
        data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(data.pop("custom_attributes", '[]'))]
        for df in ["last_usage_date", "creation_date", "change_date"]: data[df] = iso_to_dt(data[df]) if data.get(df) else None
        
        variants = []
        for var_row in self.conn.execute("SELECT * FROM translation_unit_variants WHERE tu_id = ? ORDER BY lang", (tu_id,)):
            var_data = dict(var_row)
            var_data["properties"] = [TMXProperty(**p) for p in json.loads(var_data.pop("properties", '[]'))]
            var_data["notes"] = [TMXNote(**n) for n in json.loads(var_data.pop("notes", '[]'))]
            var_data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(var_data.pop("custom_attributes", '[]'))]
            for df_v in ["creation_date", "change_date", "last_usage_date"]: var_data[df_v] = iso_to_dt(var_data[df_v]) if var_data.get(df_v) else None
            var_data.pop("id", None); var_data.pop("tu_id", None)
            variants.append(TranslationUnitVariant(**var_data))
        data["variants"] = variants
        data["id_in_db"] = data.pop("id", None); data.pop("tmx_file_id", None); data.pop("original_position", None)
        return TranslationUnit(**data)

    def get_translation_unit_by_db_id(self, tu_db_id: int) -> Optional[TranslationUnit]:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        row = self.conn.execute("SELECT * FROM translation_units WHERE id = ?", (tu_db_id,)).fetchone()
        return self._row_to_tu(row) if row else None

    def get_translation_unit_by_tuid(self, tuid: str, tmx_file_id: int) -> Optional[TranslationUnit]:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        row = self.conn.execute("SELECT * FROM translation_units WHERE tuid = ? AND tmx_file_id = ?", (tuid, tmx_file_id)).fetchone()
        return self._row_to_tu(row) if row else None

    def get_all_translation_units(self, tmx_file_id: int, offset: int = 0, limit: int = -1) -> List[TranslationUnit]:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        q = "SELECT * FROM translation_units WHERE tmx_file_id = ? ORDER BY original_position, id"
        params: List[Any] = [tmx_file_id]
        if limit > -1: q += " LIMIT ? OFFSET ?"; params.extend([limit, offset])
        return [self._row_to_tu(row) for row in self.conn.execute(q, tuple(params))]

    def update_translation_unit(self, tu_db_id: int, tu: TranslationUnit) -> bool:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        tu_data = tu.model_dump(exclude={"variants", "properties", "notes", "custom_attributes", "id_in_db"})
        tu_data.update({
            "id": tu_db_id,
            "properties": json.dumps([p.model_dump() for p in tu.properties]),
            "notes": json.dumps([n.model_dump() for n in tu.notes]),
            "custom_attributes": json.dumps([ca.model_dump() for ca in tu.custom_attributes]),
            "last_usage_date": dt_to_iso(tu.last_usage_date), "creation_date": dt_to_iso(tu.creation_date),
            "change_date": dt_to_iso(datetime.now(timezone.utc)), # Always update change_date
        })
        keys_to_update = ", ".join(f"{k} = :{k}" for k in tu_data if k != "id")
        try:
            with self.conn:
                cursor = self.conn.execute(f"UPDATE translation_units SET {keys_to_update} WHERE id = :id", tu_data)
                if cursor.rowcount == 0: return False
                self.conn.execute("DELETE FROM translation_unit_variants WHERE tu_id = ?", (tu_db_id,))
                # Re-use add_translation_unit's TUV insertion logic by temporarily creating TUVs on a TU object
                # This is a bit indirect but reuses code.
                temp_tu_for_variants = TranslationUnit(variants=tu.variants, tuid=tu.tuid) # minimal TU for variant processing
                
                for var in temp_tu_for_variants.variants: # Access variants from the passed 'tu' model
                    var_data = var.model_dump(exclude={"properties", "notes", "custom_attributes"})
                    var_data.update({
                        "tu_id": tu_db_id,
                        "properties": json.dumps([p.model_dump() for p in var.properties]),
                        "notes": json.dumps([n.model_dump() for n in var.notes]),
                        "custom_attributes": json.dumps([ca.model_dump() for ca in var.custom_attributes]),
                        "creation_date": dt_to_iso(var.creation_date), 
                        "change_date": dt_to_iso(var.change_date or datetime.now(timezone.utc)),
                        "last_usage_date": dt_to_iso(var.last_usage_date)
                    })
                    cols_v = ", ".join(var_data.keys()); placeholders_v = ", ".join(f":{k}" for k in var_data.keys())
                    self.conn.execute(f"INSERT INTO translation_unit_variants ({cols_v}) VALUES ({placeholders_v})", var_data)
            return True
        except sqlite3.Error as e: print(f"Error updating TU (db_id: {tu_db_id}): {e}"); return False


    def delete_translation_unit(self, tu_db_id: int) -> bool:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn:
                return self.conn.execute("DELETE FROM translation_units WHERE id = ?", (tu_db_id,)).rowcount > 0
        except sqlite3.Error as e: print(f"Error deleting TU (db_id: {tu_db_id}): {e}"); return False

    def get_language_codes(self, tmx_file_id: Optional[int] = None) -> List[str]:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        if tmx_file_id is not None:
            q = "SELECT DISTINCT tuv.lang FROM translation_unit_variants tuv JOIN translation_units tu ON tuv.tu_id = tu.id WHERE tu.tmx_file_id = ? ORDER BY tuv.lang"
            return [r["lang"] for r in self.conn.execute(q, (tmx_file_id,))]
        return [r["lang"] for r in self.conn.execute("SELECT DISTINCT lang FROM translation_unit_variants ORDER BY lang")]

    def get_tu_count(self, tmx_file_id: Optional[int] = None) -> int:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        q = "SELECT COUNT(*) FROM translation_units"
        params = []
        if tmx_file_id is not None: q += " WHERE tmx_file_id = ?"; params.append(tmx_file_id)
        row = self.conn.execute(q, params).fetchone()
        return row[0] if row else 0

    def update_tuv_segment(self, tu_db_id: int, lang: str, new_segment_xml: str, new_segment_text_pure: Optional[str], change_id: Optional[str] = None) -> bool:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn:
                q = "UPDATE translation_unit_variants SET segment_xml = ?, segment_text_pure = ?, change_date = ?, change_id = ? WHERE tu_id = ? AND lang = ?"
                return self.conn.execute(q, (new_segment_xml, new_segment_text_pure, dt_to_iso(datetime.now(timezone.utc)), change_id, tu_db_id, lang)).rowcount > 0
        except sqlite3.Error as e: print(f"Error updating TUV segment ({tu_db_id}, {lang}): {e}"); return False

    def update_tuv_field(self, tu_db_id: int, lang: str, field_name: str, field_value: Any, change_id: Optional[str] = None) -> bool:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        if field_name not in {"properties", "notes", "custom_attributes"}: return False
        try:
            val = json.dumps(field_value); time = dt_to_iso(datetime.now(timezone.utc))
            with self.conn:
                q = f"UPDATE translation_unit_variants SET {field_name} = ?, change_date = ?, change_id = ? WHERE tu_id = ? AND lang = ?"
                return self.conn.execute(q, (val, time, change_id, tu_db_id, lang)).rowcount > 0
        except (sqlite3.Error, TypeError, json.JSONDecodeError) as e: print(f"Error updating TUV field ({tu_db_id}, {lang}, {field_name}): {e}"); return False

    def get_translation_units_filtered(
        self,
        tmx_file_id: int,
        filter_text: Optional[str] = None,
        filter_lang_code: Optional[str] = None, # Target language for text filter
        case_sensitive_filter: bool = False,
        is_regex_filter: bool = False,
        filter_untranslated_to_other_langs: bool = False,
        source_lang_for_untranslated_filter: Optional[str] = None,
        # target_langs_for_untranslated_filter: Optional[List[str]] = None, # Complex, defer if not essential for now
        sort_by_text_in_lang_code: Optional[str] = None, # Language to sort by segment text
        sort_ascending: bool = True,
        start_offset: int = 0,
        page_size: int = 20 # Use -1 or 0 for no limit, but handle in query
    ) -> Tuple[List[TranslationUnit], int]:
        if not self.conn: raise sqlite3.OperationalError("DB not open.")

        params: List[Any] = []
        
        base_select_tu_cols = "SELECT DISTINCT tu.id AS tu_main_id, tu.* FROM translation_units tu"
        count_select = "SELECT COUNT(DISTINCT tu.id) FROM translation_units tu"
        
        joins: List[str] = []
        where_clauses: List[str] = ["tu.tmx_file_id = ?"]
        params.append(tmx_file_id)

        # Text filter
        if filter_text and filter_lang_code:
            joins.append(f"JOIN translation_unit_variants tuv_filter ON tu.id = tuv_filter.tu_id AND tuv_filter.lang = ?")
            params.append(filter_lang_code)
            
            text_col = "tuv_filter.segment_text_pure"
            op = "REGEXP" if is_regex_filter else "LIKE"
            filter_val = filter_text
            
            if not is_regex_filter and not case_sensitive_filter:
                text_col = f"LOWER({text_col})"
                filter_val = filter_val.lower()
            
            if not is_regex_filter: # Add wildcards for LIKE
                 filter_val = f"%{filter_val}%"

            where_clauses.append(f"{text_col} {op} ?")
            params.append(filter_val)

        # Untranslated filter (simplified: source exists and is non-empty, any other lang TUV is missing or empty)
        if filter_untranslated_to_other_langs and source_lang_for_untranslated_filter:
            joins.append(f"JOIN translation_unit_variants tuv_src_untrans ON tu.id = tuv_src_untrans.tu_id AND tuv_src_untrans.lang = ?")
            params.append(source_lang_for_untranslated_filter)
            where_clauses.append("tuv_src_untrans.segment_text_pure IS NOT NULL AND tuv_src_untrans.segment_text_pure != ''")
            
            # This subquery checks if there's NO target variant that is properly translated
            where_clauses.append(f"""
                NOT EXISTS (
                    SELECT 1 FROM translation_unit_variants tuv_target_untrans
                    WHERE tuv_target_untrans.tu_id = tu.id
                    AND tuv_target_untrans.lang != ? 
                    AND tuv_target_untrans.segment_text_pure IS NOT NULL 
                    AND tuv_target_untrans.segment_text_pure != ''
                )
            """)
            params.append(source_lang_for_untranslated_filter) # Exclude source lang itself from "target" check


        # Construct WHERE and JOIN clauses for both count and data queries
        join_str = " ".join(list(set(joins))) # Use set to remove duplicate joins if logic leads to it
        where_str = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Count query
        full_count_query = f"{count_select} {join_str} WHERE {where_str}"
        # print(f"Count Query: {full_count_query}, Params: {params}")
        total_matching_count = self.conn.execute(full_count_query, tuple(params)).fetchone()[0] or 0
        
        # Data query
        order_by_clauses: List[str] = []
        if sort_by_text_in_lang_code:
            # Ensure join for sorting if not already present for filtering
            sort_join_alias = "tuv_sort"
            if not any(sort_join_alias in j for j in joins): # A bit simplistic check, might need better alias management
                 joins.append(f"LEFT JOIN translation_unit_variants {sort_join_alias} ON tu.id = {sort_join_alias}.tu_id AND {sort_join_alias}.lang = ?")
                 params.append(sort_by_text_in_lang_code) # Add lang param for sort join
            
            collate_clause = "COLLATE NOCASE" if not case_sensitive_filter else "" # Assuming sort case sensitivity follows filter's
            order_by_clauses.append(f"{sort_join_alias}.segment_text_pure {collate_clause} {'ASC' if sort_ascending else 'DESC'}")
        
        order_by_clauses.append("tu.original_position ASC") # Default secondary sort
        order_by_clauses.append("tu.id ASC") # Tie-breaker

        order_by_str = "ORDER BY " + ", ".join(order_by_clauses)
        
        # Re-evaluate join_str for data query if sort added a join
        join_str_data = " ".join(list(set(joins)))

        pagination_clause = ""
        if page_size > 0:
            pagination_clause = "LIMIT ? OFFSET ?"
            params.append(page_size)
            params.append(start_offset)

        full_data_query = f"{base_select_tu_cols} {join_str_data} WHERE {where_str} {order_by_str} {pagination_clause}"
        # print(f"Data Query: {full_data_query}, Params: {params}")
        
        tu_rows = self.conn.execute(full_data_query, tuple(params)).fetchall()
        
        # Convert rows to TranslationUnit models. _row_to_tu expects a row from translation_units table.
        # Since we selected tu.*, these rows are compatible.
        tus_list = [self._row_to_tu(row) for row in tu_rows]
        
        return tus_list, total_matching_count


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
        Deletes all TUVs for a given language code within a specific TMX file.
        Returns the number of rows deleted.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        try:
            with self.conn:
                # The tu_id in translation_unit_variants links to translation_units.id
                # translation_units has tmx_file_id
                sql = """
                    DELETE FROM translation_unit_variants
                    WHERE lang = ? 
                    AND tu_id IN (SELECT id FROM translation_units WHERE tmx_file_id = ?)
                """
                cursor = self.conn.execute(sql, (lang_code, tmx_file_id))
                return cursor.rowcount
        except sqlite3.Error as e:
            print(f"Error removing language variants for lang '{lang_code}', tmx_file_id {tmx_file_id}: {e}")
            raise # Or return 0, or handle more gracefully depending on desired behavior

    def update_language_code_in_variants(
        self, old_lang_code: str, new_lang_code: str, tmx_file_id: int, current_user_id: Optional[str] = None
    ) -> int:
        """
        Updates the language code for TUVs within a specific TMX file.
        Also updates change_date and change_id.
        Returns the number of rows updated.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        current_time_iso = dt_to_iso(datetime.now(timezone.utc))
        
        try:
            with self.conn:
                # Check for conflicts first: if new_lang_code already exists for any TU that has old_lang_code.
                # This is a bit complex to check perfectly without iterating, but a simpler check can be done:
                # Does new_lang_code exist at all for this tmx_file_id? Service layer should handle this.
                # Here, we just perform the update. If UNIQUE constraint (tu_id, lang) is violated, it will fail.
                
                sql = """
                    UPDATE translation_unit_variants
                    SET lang = ?, change_date = ?, change_id = ?
                    WHERE lang = ?
                    AND tu_id IN (SELECT id FROM translation_units WHERE tmx_file_id = ?)
                """
                params = (new_lang_code, current_time_iso, current_user_id, old_lang_code, tmx_file_id)
                cursor = self.conn.execute(sql, params)
                return cursor.rowcount
        except sqlite3.IntegrityError as e:
            # This likely means a (tu_id, new_lang_code) pair already exists, violating UNIQUE constraint.
            print(f"Integrity error updating language code from '{old_lang_code}' to '{new_lang_code}' for tmx_file_id {tmx_file_id}: {e}. This might be due to new language code already existing for some TUs.")
            raise # Re-raise to be handled by service layer, or return specific error code/0 rows.
        except sqlite3.Error as e:
            print(f"Error updating language code from '{old_lang_code}' to '{new_lang_code}' for tmx_file_id {tmx_file_id}: {e}")
            raise

    # --- Methods for Chunk 5 Part 2: Content Manipulation ---

    def get_tuv(self, tu_db_id: int, lang_code: str) -> Optional[TranslationUnitVariant]:
        """
        Fetches a specific TranslationUnitVariant by its parent TU's database ID and language code.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        sql = "SELECT * FROM translation_unit_variants WHERE tu_id = ? AND lang = ?"
        row = self.conn.execute(sql, (tu_db_id, lang_code)).fetchone()
        
        if row:
            var_data = dict(row)
            # Deserialize complex fields (properties, notes, custom_attributes)
            var_data["properties"] = [TMXProperty(**p) for p in json.loads(var_data.pop("properties", '[]') or '[]')]
            var_data["notes"] = [TMXNote(**n) for n in json.loads(var_data.pop("notes", '[]') or '[]')]
            var_data["custom_attributes"] = [TMXAttribute(**ca) for ca in json.loads(var_data.pop("custom_attributes", '[]') or '[]')]
            
            # Convert datetime fields
            for df_v in ["creation_date", "change_date", "last_usage_date"]:
                 var_data[df_v] = iso_to_dt(var_data[df_v]) if var_data.get(df_v) else None
            
            var_data.pop("id", None)  # Internal DB ID of the TUV row
            var_data.pop("tu_id", None) # Foreign key to TU
            return TranslationUnitVariant(**var_data)
        return None

    def save_tuv(self, tuv: TranslationUnitVariant, tu_db_id: int, current_user_id: Optional[str] = None) -> None:
        """
        Inserts or updates a single TranslationUnitVariant.
        If a TUV for the given tu_db_id and lang already exists, it's updated. Otherwise, it's inserted.
        Manages creation_date/id only on initial insert.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")

        existing_tuv_row_id: Optional[int] = None
        cursor = self.conn.execute("SELECT id FROM translation_unit_variants WHERE tu_id = ? AND lang = ?", (tu_db_id, tuv.lang))
        row = cursor.fetchone()
        if row:
            existing_tuv_row_id = row["id"]

        now_iso = dt_to_iso(datetime.now(timezone.utc))
        
        # Prepare common data, excluding creation fields initially
        tuv_data = tuv.model_dump(exclude={"properties", "notes", "custom_attributes", "creation_date", "creation_id"})
        tuv_data.update({
            "tu_id": tu_db_id,
            "change_date": now_iso,
            "change_id": current_user_id,
            "properties": json.dumps([p.model_dump() for p in tuv.properties]),
            "notes": json.dumps([n.model_dump() for n in tuv.notes]),
            "custom_attributes": json.dumps([ca.model_dump() for ca in tuv.custom_attributes]),
            "last_usage_date": dt_to_iso(tuv.last_usage_date) # Ensure this is correctly handled
        })

        try:
            with self.conn:
                if existing_tuv_row_id is not None: # Update existing TUV
                    tuv_data["id"] = existing_tuv_row_id # For the WHERE clause
                    
                    # Fields to update (exclude creation_date, creation_id, tu_id, lang from direct SET)
                    update_fields = {k: v for k, v in tuv_data.items() if k not in ["id", "tu_id", "lang", "creation_date", "creation_id"]}
                    set_clauses = ", ".join([f"{key} = :{key}" for key in update_fields.keys()])
                    
                    update_sql = f"UPDATE translation_unit_variants SET {set_clauses} WHERE id = :id"
                    self.conn.execute(update_sql, {**update_fields, "id": existing_tuv_row_id})
                
                else: # Insert new TUV
                    # Add creation fields for new insert
                    tuv_data["creation_date"] = dt_to_iso(tuv.creation_date) if tuv.creation_date else now_iso
                    tuv_data["creation_id"] = tuv.creation_id # Can be None
                    
                    # Ensure all fields required by the table are present
                    cols = ", ".join(tuv_data.keys())
                    placeholders = ", ".join(f":{k}" for k in tuv_data.keys())
                    insert_sql = f"INSERT INTO translation_unit_variants ({cols}) VALUES ({placeholders})"
                    self.conn.execute(insert_sql, tuv_data)
        except sqlite3.Error as e:
            print(f"Error saving TUV for tu_db_id {tu_db_id}, lang {tuv.lang}: {e}")
            raise

    def delete_untranslated_tus(self, source_lang_code: str, tmx_file_id: int) -> int:
        """
        Deletes TUs where the source language variant exists and is non-empty,
        but all other language variants for that TU are either non-existent or empty.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        sql_select_ids = """
            SELECT tu.id
            FROM translation_units tu
            JOIN translation_unit_variants src_tuv ON tu.id = src_tuv.tu_id 
            WHERE tu.tmx_file_id = ? 
              AND src_tuv.lang = ?
              AND (src_tuv.segment_text_pure IS NOT NULL AND src_tuv.segment_text_pure != '') 
              AND NOT EXISTS (
                  SELECT 1
                  FROM translation_unit_variants other_tuv
                  WHERE other_tuv.tu_id = tu.id
                    AND other_tuv.lang != ?
                    AND (other_tuv.segment_text_pure IS NOT NULL AND other_tuv.segment_text_pure != '')
              );
        """
        deleted_count = 0
        try:
            with self.conn:
                tu_ids_to_delete = [row["id"] for row in self.conn.execute(sql_select_ids, (tmx_file_id, source_lang_code, source_lang_code))]
                
                if not tu_ids_to_delete:
                    return 0
                
                # Use a placeholder string for multiple IDs: (?, ?, ...)
                placeholders = ', '.join('?' for _ in tu_ids_to_delete)
                
                # ON DELETE CASCADE will handle TUVs
                delete_tu_sql = f"DELETE FROM translation_units WHERE id IN ({placeholders})"
                cursor = self.conn.execute(delete_tu_sql, tu_ids_to_delete)
                deleted_count = cursor.rowcount
            return deleted_count
        except sqlite3.Error as e:
            print(f"Error deleting untranslated TUs for tmx_file_id {tmx_file_id}, source_lang {source_lang_code}: {e}")
            raise

    def clear_variants_matching_source(self, source_lang_code: str, tmx_file_id: int, current_user_id: Optional[str]) -> int:
        """
        Clears target TUVs if their text matches the source TUV's text for the same TU.
        "Clearing" means setting segment_xml and segment_text_pure to empty strings.
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")
        
        # This operation is complex to do in pure SQL efficiently without window functions or CTEs that might not be ideal for all SQLite versions.
        # Iterative approach in Python:
        all_tus = self.get_all_translation_units(tmx_file_id=tmx_file_id) # Fetches full TU objects
        cleared_count = 0
        
        empty_segment_xml = "<seg></seg>" # Standard empty segment
        now_iso = dt_to_iso(datetime.now(timezone.utc))

        try:
            with self.conn:
                for tu in all_tus:
                    source_tuv: Optional[TranslationUnitVariant] = None
                    for variant in tu.variants:
                        if variant.lang == source_lang_code:
                            source_tuv = variant
                            break
                    
                    if source_tuv and source_tuv.segment_text_pure: # Source must exist and be non-empty
                        for target_tuv_model in tu.variants:
                            if target_tuv_model.lang != source_lang_code and \
                               target_tuv_model.segment_text_pure == source_tuv.segment_text_pure:
                                
                                # Need TUV's own DB ID to update it directly. _row_to_tu doesn't populate TUV.id
                                # This requires fetching the TUV's id first, or modifying _row_to_tu to include it (not ideal)
                                # For now, we'll update based on tu_id and lang, which is fine.
                                update_sql = """
                                    UPDATE translation_unit_variants
                                    SET segment_xml = ?, segment_text_pure = ?, change_date = ?, change_id = ?
                                    WHERE tu_id = ? AND lang = ?
                                """
                                cursor = self.conn.execute(update_sql, (
                                    empty_segment_xml, "", now_iso, current_user_id, 
                                    tu.id_in_db, target_tuv_model.lang
                                ))
                                cleared_count += cursor.rowcount
            return cleared_count
        except sqlite3.Error as e:
            print(f"Error clearing variants matching source for tmx_file_id {tmx_file_id}: {e}")
            raise

    def delete_duplicate_tus(self, tmx_file_id: int) -> int:
        """
        Deletes duplicate TUs based on a signature derived from their variants' lang and segment_text_pure.
        Keeps the TU with the smallest original_position (then smallest id_in_db if positions are same or null).
        """
        if not self.conn: raise sqlite3.OperationalError("DB not open.")

        # Fetch TUs with their variants. Order by original_position, then id to ensure we keep the "first" one.
        # The get_all_translation_units already sorts by original_position, id.
        all_tus = self.get_all_translation_units(tmx_file_id=tmx_file_id, limit=-1) # Get all
        
        seen_signatures: Dict[str, int] = {} # signature -> tu_db_id_to_keep
        ids_to_delete: List[int] = []
        
        for tu in all_tus:
            if tu.id_in_db is None: continue # Should not happen for fetched TUs

            # Create signature: sorted list of (lang, text_pure) tuples
            variant_signatures = []
            for var in sorted(tu.variants, key=lambda v: v.lang): # Sort by lang for consistent signature
                variant_signatures.append((var.lang, var.segment_text_pure or ""))
            
            # Signature is a string representation of this sorted list of tuples
            # Using json.dumps for a canonical string form of the list of tuples
            signature = json.dumps(variant_signatures)

            if signature in seen_signatures:
                ids_to_delete.append(tu.id_in_db)
            else:
                seen_signatures[signature] = tu.id_in_db
        
        deleted_count = 0
        if ids_to_delete:
            try:
                with self.conn:
                    placeholders = ', '.join('?' for _ in ids_to_delete)
                    delete_sql = f"DELETE FROM translation_units WHERE id IN ({placeholders})"
                    cursor = self.conn.execute(delete_sql, ids_to_delete)
                    deleted_count = cursor.rowcount
            except sqlite3.Error as e:
                print(f"Error deleting duplicate TUs for tmx_file_id {tmx_file_id}: {e}")
                raise
        
        return deleted_count
