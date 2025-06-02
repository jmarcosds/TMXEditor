import os
import shutil # For file operations like copy, move, delete
import re # For language code validation and other regex operations
import sqlite3 # For catching specific database errors like IntegrityError
import json # For metadata methods if they were to handle raw JSON strings (currently Pydantic handles this)
from datetime import datetime, timezone 
from typing import Optional, Dict, Any, List, Iterable, Tuple
from collections import defaultdict # Used in consolidate_units
from lxml import etree # Used by _create_segment_xml_str (though this helper might be better in parser)

from .storage import SQLiteStore
from .tmx_parser import TMXParser, extract_pure_text_from_segment_xml
# from .tmx_converter import TMXConverter # Placeholder for future converter integration
from .models import (
    TMXHeader, TranslationUnit, TranslationUnitVariant, FileInfoResponse, 
    TMXAttribute, TMXProperty, TMXNote # Ensure all necessary models are imported
)
from .config import ACTIVE_DB_PATH # Path for the active session database
from .exceptions import ( 
    TMXServerError, SessionInactiveError, TMXParsingError, 
    InvalidOperationError, ResourceNotFoundError, DatabaseError
)


class TMXService:
    """
    Service layer for managing TMX file operations.
    It orchestrates parsing, storage, retrieval, and manipulation of TMX data,
    acting as an intermediary between the API layer (e.g., FastAPI endpoints)
    and the data storage layer (SQLiteStore).

    An instance of TMXService typically manages a single active TMX file session
    at a time, identified by `_current_tmx_file_id`.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initializes the TMXService.

        Args:
            db_path (Optional[str]): Path to the SQLite database file. 
                                     If None, uses the path from `ACTIVE_DB_PATH` config.
                                     This allows overriding the DB path, e.g., for testing.
        """
        self.db_path: str = db_path if db_path is not None else ACTIVE_DB_PATH
        self.store: Optional[SQLiteStore] = None
        self.parser: TMXParser = TMXParser()
        # self.converter: Optional[TMXConverter] = None # Initialize if/when TMXConverter is integrated

        self._is_active: bool = False # Tracks if a TMX file session is currently active
        self._current_tmx_file_id: Optional[int] = None # DB ID of the currently active TMX file
        self._current_file_path: Optional[str] = None # Filesystem path of the currently open file

    def _init_store(self, for_new_file: bool = True, new_file_path: Optional[str] = None) -> None:
        """
        Initializes or re-initializes the SQLiteStore for a TMX file session.

        If `for_new_file` is True (default), it prepares the store for a new TMX file by
        clearing any existing data from relevant tables and creating a new file entry
        in the `tmx_files` table. The `new_file_path` is associated with this new session.

        If `for_new_file` is False, it would imply re-opening an existing database without clearing,
        though this mode is not fully utilized by current service methods like `open_file`.

        Args:
            for_new_file (bool): If True, clears the database for a new file session.
            new_file_path (Optional[str]): The filesystem path for the new TMX file.
                                           Used to create the entry in `tmx_files`.
        """
        if self.store: # Close any existing store connection first
            self.store.close()
        
        self.store = SQLiteStore(self.db_path) # Initialize new store instance
        self._is_active = False 
        self._current_tmx_file_id = None
        self._current_file_path = None

        if for_new_file:
            if not new_file_path:
                # Fallback, though `open_file` should always provide a path.
                new_file_path = f"session_{datetime.now(timezone.utc).timestamp()}.tmx" 
            
            # `prepare_new_tmx_file_session` clears tables and creates a new `tmx_files` entry.
            self._current_tmx_file_id = self.store.prepare_new_tmx_file_session(new_file_path)
            self._current_file_path = new_file_path 
        # If not for_new_file, it implies re-opening an existing DB state (e.g., application restart).
        # This path would require logic to load the last active tmx_file_id and its path. (Not implemented here)

    def _get_active_store(self) -> SQLiteStore:
        """
        Ensures and returns the active SQLiteStore instance.

        Returns:
            SQLiteStore: The active SQLiteStore instance.

        Raises:
            SessionInactiveError: If no TMX file session is active or the store is not initialized.
        """
        if not self.store or not self._is_active or self._current_tmx_file_id is None:
            raise SessionInactiveError("No active TMX session or database store.")
        return self.store

    def _ensure_active_tmx_file_id(self) -> int:
        """
        Ensures that there is a valid `_current_tmx_file_id` for the active session.

        Returns:
            int: The current TMX file ID.

        Raises:
            SessionInactiveError: If no TMX file ID is set for the active session.
        """
        if self._current_tmx_file_id is None or not self._is_active:
            raise SessionInactiveError("No active TMX file ID. Session may not be properly initialized.")
        return self._current_tmx_file_id

    def open_file(self, file_path: str) -> None:
        """
        Opens a TMX file from the given path, parses its content, and loads it into
        the database, establishing a new active session.

        This involves:
        1. Initializing a new store session (clearing previous data).
        2. Parsing the TMX file into Pydantic models (`TMXHeader`, `TranslationUnit`).
        3. Extracting pure text from TUV segments.
        4. Saving the header and translation units to the database via `SQLiteStore`.
        5. Marking the session as active.

        Args:
            file_path (str): The absolute or relative path to the TMX file.

        Raises:
            FileNotFoundError: If the specified `file_path` does not exist.
            TMXParsingError: If the TMX file is malformed or parsing fails.
            DatabaseError: If database operations fail during loading.
            TMXServerError: For other unexpected errors during the process.
        """
        if not os.path.exists(file_path): # Validate file existence early
            raise FileNotFoundError(f"TMX file not found at path: {file_path}")

        self._init_store(for_new_file=True, new_file_path=file_path)
        
        if self._current_tmx_file_id is None: 
             raise TMXServerError("Failed to initialize TMX file session ID during open_file.")

        try:
            try: 
                header_model, tu_models_iterable = self.parser.parse_tmx_file(file_path)
            except ValueError as ve: 
                raise TMXParsingError(f"Failed to parse TMX file: {file_path}", details=str(ve)) from ve
            
            processed_tus_for_storage: List[TranslationUnit] = []
            for tu_model in tu_models_iterable: 
                for variant_model in tu_model.variants:
                    variant_model.segment_text_pure = extract_pure_text_from_segment_xml(variant_model.segment_xml)
                processed_tus_for_storage.append(tu_model)

            if self.store: 
                self.store.save_header(header_model, tmx_file_id=self._current_tmx_file_id)
                self.store.add_translation_units(processed_tus_for_storage, tmx_file_id=self._current_tmx_file_id)
                self._is_active = True 
            else: 
                raise DatabaseError("SQLiteStore not initialized during open_file, cannot save data.")

        except (TMXParsingError, DatabaseError): 
            self.close_file() 
            raise
        except Exception as e: 
            self.close_file() 
            raise TMXServerError(f"An unexpected error occurred while opening or processing TMX file {file_path}.", details=str(e)) from e


    def close_file(self) -> None:
        """
        Closes the current TMX file session.
        This involves closing the database connection and resetting service state variables
        related to the active session.
        """
        if self.store: 
            self.store.close()
            self.store = None
        self._is_active = False
        self._current_tmx_file_id = None
        self._current_file_path = None

    def save_file(self, output_file_path: str, indentation: Optional[int] = 2) -> None:
        """
        Saves the currently active TMX data (header and all translation units)
        to a specified TMX file path.

        Args:
            output_file_path (str): The path where the TMX file will be saved.
            indentation (Optional[int]): Number of spaces for XML pretty printing.
                                         Defaults to 2. If None, a default might be chosen by parser.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If the header is missing for the active TMX file.
            DatabaseError: If there's an issue retrieving data from the store.
            TMXServerError: For issues during file writing by the parser.
        """
        store = self._get_active_store() 
        tmx_id = self._ensure_active_tmx_file_id() 

        header_model = store.get_header(tmx_id)
        if header_model is None:
            raise InvalidOperationError("Cannot save TMX file: No header found for the active session.")
        
        tu_models_list = store.get_all_translation_units(tmx_id, limit=-1) 

        indent_val = indentation if indentation is not None else 2 

        try:
            self.parser.write_tmx_file(output_file_path, header_model, tu_models_list, indentation=indent_val)
        except Exception as e: 
            raise TMXServerError(f"Failed to write TMX file to '{output_file_path}'.", details=str(e)) from e

    def get_file_info(self) -> FileInfoResponse:
        """
        Retrieves information about the currently active TMX file session,
        including its path, header, TU count, and language codes.

        Returns:
            FileInfoResponse: A Pydantic model containing details of the active session.
                              If no session is active, `is_active` will be False.
        
        Note:
            If an error occurs while fetching details for an active session, this method
            currently returns an `FileInfoResponse` with `is_active=False` and some path/ID info.
        """
        if not self._is_active or not self.store or self._current_tmx_file_id is None or self._current_file_path is None:
            return FileInfoResponse(is_active=False)

        try:
            header = self.store.get_header(self._current_tmx_file_id)
            language_codes = self.store.get_language_codes(self._current_tmx_file_id)
            tu_count = self.store.get_tu_count(self._current_tmx_file_id)

            return FileInfoResponse(
                is_active=True,
                file_path=self._current_file_path,
                tmx_file_id=self._current_tmx_file_id,
                header=header, 
                tu_count=tu_count,
                language_codes=language_codes
            )
        except Exception as e: 
            return FileInfoResponse(
                is_active=False, 
                file_path=self._current_file_path, 
                tmx_file_id=self._current_tmx_file_id, 
                error_message=f"Failed to retrieve full details: {str(e)}" 
            )

    def open_file_from_models(self, header_model: TMXHeader, tu_models_list: List[TranslationUnit], original_filepath: str = "converted_data.tmx") -> None:
        """
        Initializes a new TMX session directly from Pydantic models (TMXHeader, list of TranslationUnit).
        This is useful for loading data that has been converted from other formats (e.g., CSV, Excel)
        into the standard TMX model structure before persisting to the database.

        Args:
            header_model (TMXHeader): The TMXHeader model.
            tu_models_list (List[TranslationUnit]): A list of TranslationUnit models.
            original_filepath (str): A nominal filepath to associate with this data session
                                     (e.g., "csv_import.tmx", "excel_import.tmx").

        Raises:
            TMXServerError: If session initialization fails.
            DatabaseError: If database operations fail during loading.
        """
        self._init_store(for_new_file=True, new_file_path=original_filepath)
        
        if self._current_tmx_file_id is None: 
             raise TMXServerError("Failed to initialize TMX file session ID for model import.")

        try:
            processed_tus_for_storage: List[TranslationUnit] = []
            for tu_model in tu_models_list:
                for variant_model in tu_model.variants:
                    if variant_model.segment_text_pure is None and variant_model.segment_xml:
                         variant_model.segment_text_pure = extract_pure_text_from_segment_xml(variant_model.segment_xml)
                    elif variant_model.segment_text_pure is None: 
                         variant_model.segment_text_pure = "" 
                processed_tus_for_storage.append(tu_model)

            if self.store: 
                self.store.save_header(header_model, tmx_file_id=self._current_tmx_file_id)
                self.store.add_translation_units(processed_tus_for_storage, tmx_file_id=self._current_tmx_file_id)
                self._is_active = True 
            else: 
                raise DatabaseError("SQLiteStore not initialized during open_file_from_models.")

        except sqlite3.Error as e_db:
            self.close_file() 
            raise DatabaseError(f"Database error opening TMX data from models (source: {original_filepath}).", details=str(e_db)) from e_db
        except Exception as e:
            self.close_file() 
            raise TMXServerError(f"Error opening TMX data from models (source: {original_filepath}).", details=str(e)) from e

    def __del__(self):
        """
        Destructor for TMXService. Ensures the database connection is closed
        when the service instance is garbage collected.
        """
        self.close_file()

    # --- Language Operations ---

    def add_language(self, lang_code: str) -> Dict[str, str]:
        """
        Validates a language code format and conceptually prepares for its use.
        In the current system, languages are implicitly "added" when a TUV with that
        language code is created. This method primarily serves as a validation step.
        Future enhancements might involve updating a declared list of languages in the TMX header.

        Args:
            lang_code (str): The language code to validate (e.g., "de-DE", "es").

        Returns:
            Dict[str, str]: A dictionary with status and message.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If the language code format is invalid.
        """
        self._ensure_active_tmx_file_id() 

        if not re.fullmatch(r"^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$", lang_code):
            raise InvalidOperationError(f"Invalid language code format: '{lang_code}'. Adhere to BCP 47 (e.g., 'en', 'pt-BR').")

        return {"status": "success", "message": f"Language code '{lang_code}' format is valid."}

    def remove_language(self, lang_code: str) -> Dict[str, str]:
        """
        Removes all Translation Unit Variants (TUVs) for a specified language code
        from the currently active TMX file.

        Args:
            lang_code (str): The language code of the variants to be removed.

        Returns:
            Dict[str, str]: A dictionary confirming the operation's success and details.

        Raises:
            SessionInactiveError: If no TMX session is active.
            DatabaseError: If a database error occurs during deletion.
            TMXServerError: For other unexpected errors.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        
        try:
            deleted_count = store.remove_language_variants(lang_code, tmx_id)
            return {"status": "success", "message": f"{deleted_count} TUVs for language '{lang_code}' removed."}
        except sqlite3.Error as e_db: 
            raise DatabaseError(f"Database error removing language '{lang_code}'.", details=str(e_db)) from e_db
        except Exception as e:
            raise TMXServerError(f"Unexpected error removing language '{lang_code}'.", details=str(e)) from e


    def change_language_code(
        self, old_lang_code: str, new_lang_code: str, current_user_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Changes all occurrences of an old language code to a new language code for TUVs
        within the currently active TMX file. Updates `change_date` and `change_id` on modified TUVs.

        Args:
            old_lang_code (str): The existing language code to be replaced.
            new_lang_code (str): The new language code to apply.
            current_user_id (Optional[str]): Identifier for the user initiating the change.

        Returns:
            Dict[str, str]: Confirmation message with counts of updated TUVs.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If language codes are identical, new code format is invalid,
                                   or if the new language code already exists (causing conflict).
            ResourceNotFoundError: If the old language code does not exist in the file.
            DatabaseError: If a database integrity error (other than conflict) occurs.
            TMXServerError: For other unexpected errors.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        if old_lang_code == new_lang_code:
            raise InvalidOperationError("Old and new language codes cannot be the same.")

        if not re.fullmatch(r"^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$", new_lang_code):
            raise InvalidOperationError(f"Invalid new language code format: '{new_lang_code}'.")

        existing_langs = store.get_language_codes(tmx_id)
        if old_lang_code not in existing_langs:
            raise ResourceNotFoundError(f"The old language code '{old_lang_code}' does not exist in the current TMX file.")
        if new_lang_code in existing_langs:
            raise InvalidOperationError(f"The new language code '{new_lang_code}' already exists. Choose a different code.")

        try:
            updated_count = store.update_language_code_in_variants(
                old_lang_code, new_lang_code, tmx_id, current_user_id
            )
            return {
                "status": "success",
                "message": f"{updated_count} TUVs updated from language '{old_lang_code}' to '{new_lang_code}'."
            }
        except sqlite3.IntegrityError as e_int: 
            raise InvalidOperationError(
                f"Failed to change language code due to a conflict: '{new_lang_code}' might already exist for some TUs.",
                details=str(e_int)
            ) from e_int
        except DatabaseError as e_db: 
            raise DatabaseError(f"Database error changing language code from '{old_lang_code}' to '{new_lang_code}'.", details=str(e_db)) from e_db
        except Exception as e: 
            raise TMXServerError(f"Unexpected error changing language code from '{old_lang_code}' to '{new_lang_code}'.", details=str(e)) from e

    def set_source_language(self, lang_code: str, current_user_id: Optional[str] = None) -> Dict[str, str]:
        """
        Sets the source language (`srclang`) attribute in the TMX header for the active file.
        This also updates the header's `change_date` and `change_id`.

        Args:
            lang_code (str): The new source language code to set.
            current_user_id (Optional[str]): Identifier for the user making the change.

        Returns:
            Dict[str, str]: Confirmation of the operation.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If the language code format is invalid or header is missing.
            DatabaseError: If saving the updated header fails.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        if not re.fullmatch(r"^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$", lang_code):
            raise InvalidOperationError(f"Invalid language code format: '{lang_code}'.")

        header = store.get_header(tmx_id)
        if not header:
            raise InvalidOperationError("Active TMX file is missing its header. Cannot set source language.")
        
        header.srclang = lang_code 
        header.change_date = datetime.now(timezone.utc) 
        header.change_id = current_user_id

        try:
            store.save_header(header, tmx_id) 
            return {"status": "success", "message": f"Source language successfully set to '{lang_code}'."}
        except DatabaseError as e_db:
            raise DatabaseError(f"Database error setting source language to '{lang_code}'.", details=str(e_db)) from e_db

    # --- Content Manipulation Operations ---

    def _create_empty_segment_xml(self) -> str:
        """
        Helper function to generate a string representation of an empty TMX <seg> element.
        
        Returns:
            str: The string "<seg></seg>".
        """
        return "<seg></seg>"

    def consolidate_units(self, source_lang_code: str, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Consolidates duplicate Translation Units (TUs) based on identical source text
        in the specified `source_lang_code`.

        When duplicates are found:
        - The TU with the lowest `original_position` (or `id_in_db` as tie-breaker) is kept as the "master".
        - Variants from duplicate TUs are merged into the master TU if the master does not
          already have a variant for that language or if the master's variant is empty.
        - Duplicate TUs are deleted after their translatable content is merged.
        - The master TU's `change_date` and `change_id` are updated if it's modified.

        Args:
            source_lang_code (str): The language code of the source text to use for identifying duplicates.
            current_user_id (Optional[str]): Identifier for the user performing the operation.

        Returns:
            Dict[str, Any]: A summary of the consolidation, including counts of merged/updated
                            and deleted TUs.
        
        Raises:
            SessionInactiveError: If no TMX session is active.
            DatabaseError: If database operations fail.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        all_tus = store.get_all_translation_units(tmx_file_id=tmx_id, limit=-1) 
        
        tus_by_source_text: Dict[str, List[TranslationUnit]] = defaultdict(list)
        for tu_model in all_tus:
            source_variant = next((v for v in tu_model.variants if v.lang == source_lang_code), None)
            if source_variant and source_variant.segment_text_pure: 
                tus_by_source_text[source_variant.segment_text_pure].append(tu_model)
        
        merged_or_updated_master_tus = 0
        deleted_duplicate_tus = 0
        
        all_lang_codes = store.get_language_codes(tmx_id)
        target_lang_codes = [lc for lc in all_lang_codes if lc != source_lang_code]

        for source_text_key, group_of_identical_source_tus in tus_by_source_text.items():
            if len(group_of_identical_source_tus) <= 1:
                continue 

            group_of_identical_source_tus.sort(key=lambda tu: (
                getattr(tu, 'original_position', float('inf')), 
                tu.id_in_db or float('inf') 
            ))
            master_tu_model = group_of_identical_source_tus[0]
            
            if master_tu_model.id_in_db is None: continue 

            master_tu_was_modified = False
            for i in range(1, len(group_of_identical_source_tus)): 
                duplicate_tu_model = group_of_identical_source_tus[i]
                if duplicate_tu_model.id_in_db is None: continue

                for target_lang in target_lang_codes:
                    master_target_variant = next((v for v in master_tu_model.variants if v.lang == target_lang), None)
                    duplicate_target_variant = next((v for v in duplicate_tu_model.variants if v.lang == target_lang), None)

                    if duplicate_target_variant and duplicate_target_variant.segment_text_pure:
                        if master_target_variant is None or not master_target_variant.segment_text_pure:
                            new_variant_for_master = TranslationUnitVariant(
                                lang=target_lang,
                                segment_xml=duplicate_target_variant.segment_xml,
                                segment_text_pure=duplicate_target_variant.segment_text_pure,
                                creation_date=duplicate_target_variant.creation_date, 
                                creation_id=duplicate_target_variant.creation_id,
                                change_id=current_user_id 
                            )
                            store.save_tuv(new_variant_for_master, master_tu_model.id_in_db, current_user_id)
                            master_tu_was_modified = True
                
                store.delete_translation_unit(duplicate_tu_model.id_in_db)
                deleted_duplicate_tus += 1
            
            if master_tu_was_modified:
                merged_or_updated_master_tus += 1
                reloaded_master_tu = store.get_translation_unit_by_db_id(master_tu_model.id_in_db)
                if reloaded_master_tu:
                    reloaded_master_tu.change_date = datetime.now(timezone.utc)
                    reloaded_master_tu.change_id = current_user_id
                    store.update_translation_unit(reloaded_master_tu.id_in_db, reloaded_master_tu)

        return {
            "status": "success",
            "message": f"Consolidation based on source '{source_lang_code}' complete. Master TUs updated/merged into: {merged_or_updated_master_tus}. Redundant TUs deleted: {deleted_duplicate_tus}.",
            "master_tus_updated_count": merged_or_updated_master_tus,
            "duplicate_tus_deleted_count": deleted_duplicate_tus
        }

    def remove_untranslated_units(self, source_lang_code: str) -> Dict[str, Any]:
        """
        Removes Translation Units (TUs) that have a non-empty source language variant
        but are effectively untranslated in all other target languages (i.e., target variants
        are missing or their pure text is empty).

        Args:
            source_lang_code (str): The language code considered as the source.

        Returns:
            Dict[str, Any]: A summary of the operation, including the count of deleted TUs.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If the source language code format is invalid.
            DatabaseError: If database operations fail.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        
        if not re.fullmatch(r"^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$", source_lang_code):
            raise InvalidOperationError(f"Invalid source language code format: '{source_lang_code}'.")

        try:
            deleted_count = store.delete_untranslated_tus(source_lang_code, tmx_id)
            return {
                "status": "success",
                "message": f"{deleted_count} TUs (untranslated from source '{source_lang_code}') removed.",
                "deleted_count": deleted_count
            }
        except DatabaseError as e_db:
            raise DatabaseError(f"Database error removing untranslated units for source '{source_lang_code}'.", details=str(e_db)) from e_db

    def remove_variants_same_as_source(self, source_lang_code: str, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Clears target language TUVs if their pure text content is identical to the
        source language TUV's pure text content for the same TU.
        "Clearing" sets `segment_xml` to empty and `segment_text_pure` to an empty string,
        and updates `change_date` and `change_id`.

        Args:
            source_lang_code (str): The source language to compare against.
            current_user_id (Optional[str]): Identifier for the user making the change.

        Returns:
            Dict[str, Any]: Summary of the operation, including count of cleared TUVs.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If the source language code format is invalid.
            DatabaseError: If database operations fail.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        if not re.fullmatch(r"^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$", source_lang_code):
            raise InvalidOperationError(f"Invalid source language code format: '{source_lang_code}'.")

        try:
            cleared_count = store.clear_variants_matching_source(source_lang_code, tmx_id, current_user_id)
            return {
                "status": "success",
                "message": f"{cleared_count} target TUVs identical to source '{source_lang_code}' were cleared.",
                "cleared_count": cleared_count
            }
        except DatabaseError as e_db:
            raise DatabaseError(f"Database error clearing variants matching source '{source_lang_code}'.", details=str(e_db)) from e_db

    def remove_duplicate_units(self) -> Dict[str, Any]:
        """
        Removes fully duplicate Translation Units (TUs). A TU is considered a duplicate if it has
        the exact same set of variants (language code and pure segment text) as another TU.
        The TU with the lower `original_position` (or `id` as a tie-breaker) is preserved.

        Returns:
            Dict[str, Any]: A summary of the operation, including the count of deleted TUs.

        Raises:
            SessionInactiveError: If no TMX session is active.
            DatabaseError: If database operations fail.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        try:
            deleted_count = store.delete_duplicate_tus(tmx_id)
            return {
                "status": "success",
                "message": f"{deleted_count} fully duplicate TUs removed.",
                "deleted_count": deleted_count
            }
        except DatabaseError as e_db:
            raise DatabaseError("Database error removing duplicate TUs.", details=str(e_db)) from e_db

    # --- Metadata and Segment Text Editing ---

    def _create_segment_xml_str(self, text_content: str) -> str:
        """
        Creates a simple TMX segment XML string (e.g., "<seg>text</seg>") from plain text.
        Uses `lxml.etree` to handle XML special characters correctly within the text content.

        Args:
            text_content (str): The plain text to be wrapped in <seg> tags.

        Returns:
            str: An XML string representing the segment.
        """
        seg_element = etree.Element("seg") 
        seg_element.text = text_content    
        return etree.tostring(seg_element, encoding="unicode", xml_declaration=False)


    def remove_all_tags_from_segments(self, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Removes all XML/HTML tags from the `segment_xml` of all TUVs in the active TMX file,
        leaving only the pure text content. Updates `segment_xml` and `segment_text_pure`
        accordingly, along with change metadata.

        Args:
            current_user_id (Optional[str]): Identifier for the user performing the operation.

        Returns:
            Dict[str, Any]: Summary of the operation, including count of modified TUVs.
        
        Raises:
            SessionInactiveError: If no TMX session is active.
            DatabaseError: If database operations fail.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        try:
            modified_tuv_count = store.strip_tags_from_all_segments(tmx_id, current_user_id)
            return {
                "status": "success",
                "message": f"{modified_tuv_count} TUVs had their segment tags stripped.",
                "modified_tuv_count": modified_tuv_count
            }
        except DatabaseError as e_db:
            raise DatabaseError("Database error removing tags from segments.", details=str(e_db)) from e_db


    def remove_leading_trailing_spaces_from_segments(self, current_user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Removes leading and trailing whitespace from the `segment_text_pure` and updates
        the `segment_xml` accordingly for all TUVs in the active TMX file.
        Change metadata is also updated.

        Args:
            current_user_id (Optional[str]): Identifier for the user performing the operation.

        Returns:
            Dict[str, Any]: Summary of the operation, including count of modified TUVs.

        Raises:
            SessionInactiveError: If no TMX session is active.
            DatabaseError: If database operations fail.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        try:
            modified_tuv_count = store.strip_spaces_from_all_segments(tmx_id, current_user_id)
            return {
                "status": "success",
                "message": f"{modified_tuv_count} TUVs had leading/trailing spaces removed from segments.",
                "modified_tuv_count": modified_tuv_count
            }
        except DatabaseError as e_db:
            raise DatabaseError("Database error removing leading/trailing spaces from segments.", details=str(e_db)) from e_db


    def search_and_replace(
        self, 
        search_text: str, 
        replace_text: str, 
        lang_code: Optional[str], 
        is_regex: bool, 
        case_sensitive: bool, 
        current_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs search and replace on the pure text content (`segment_text_pure`) of TUVs.
        If a language code is specified, it targets only TUVs of that language. Otherwise,
        it applies to all TUVs in the active TMX file.
        The `segment_xml` is updated based on the modified pure text (by creating a new simple <seg>).
        Change metadata is also updated.

        Args:
            search_text (str): The text or regex pattern to search for.
            replace_text (str): The text to replace matches with.
            lang_code (Optional[str]): Specific language code to target, or None for all.
            is_regex (bool): True if `search_text` is a regex pattern.
            case_sensitive (bool): True for case-sensitive search.
            current_user_id (Optional[str]): Identifier for the user performing the operation.

        Returns:
            Dict[str, Any]: Summary of the operation, including count of modified TUVs.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If search parameters are invalid (e.g., empty non-regex search).
            DatabaseError: If database operations fail.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()
        
        if not search_text and not is_regex:
            raise InvalidOperationError("Search text cannot be empty for a non-regular expression search.")

        try:
            modified_tuv_count = store.search_and_replace_in_segments(
                tmx_file_id=tmx_id,
                search_text=search_text,
                replace_text=replace_text,
                lang_code=lang_code, 
                is_regex=is_regex,
                case_sensitive=case_sensitive,
                current_user_id=current_user_id
            )
            return {
                "status": "success",
                "message": f"{modified_tuv_count} TUVs were modified by search and replace.",
                "modified_tuv_count": modified_tuv_count
            }
        except DatabaseError as e_db:
            raise DatabaseError("Database error during search and replace operation.", details=str(e_db)) from e_db
        except InvalidOperationError: 
            raise
        except Exception as e: 
            raise TMXServerError("An unexpected error occurred during search and replace.", details=str(e)) from e


    def get_tu_metadata(self, tu_db_id: int) -> Dict[str, Any]:
        """
        Retrieves metadata (custom attributes, properties, notes) for a specific Translation Unit (TU).

        Args:
            tu_db_id (int): The database ID of the TU.

        Returns:
            Dict[str, Any]: A dictionary containing the TU's TUID, database ID, and its
                            deserialized metadata fields.

        Raises:
            SessionInactiveError: If no TMX session is active.
            ResourceNotFoundError: If the TU with the given `tu_db_id` is not found.
        """
        store = self._get_active_store()
        tu_model = store.get_translation_unit_by_db_id(tu_db_id)
        if not tu_model:
            raise ResourceNotFoundError(f"Translation Unit with DB ID {tu_db_id} not found.")
        return {
            "tuid": tu_model.tuid,
            "id_in_db": tu_model.id_in_db, 
            "custom_attributes": [attr.model_dump() for attr in tu_model.custom_attributes],
            "properties": [prop.model_dump() for prop in tu_model.properties],
            "notes": [note.model_dump() for note in tu_model.notes]
        }

    def get_tuv_metadata(self, tu_db_id: int, lang_code: str) -> Dict[str, Any]:
        """
        Retrieves metadata (custom attributes, properties, notes) for a specific
        Translation Unit Variant (TUV).

        Args:
            tu_db_id (int): The database ID of the parent TU.
            lang_code (str): The language code of the TUV.

        Returns:
            Dict[str, Any]: A dictionary containing the TUV's language and its
                            deserialized metadata fields.

        Raises:
            SessionInactiveError: If no TMX session is active.
            ResourceNotFoundError: If the TUV is not found.
        """
        store = self._get_active_store()
        tuv_model = store.get_tuv(tu_db_id, lang_code) 
        if not tuv_model:
            raise ResourceNotFoundError(f"TUV for TU DB ID {tu_db_id} and language '{lang_code}' not found.")
        return {
            "lang": tuv_model.lang,
            "custom_attributes": [attr.model_dump() for attr in tuv_model.custom_attributes],
            "properties": [prop.model_dump() for prop in tuv_model.properties],
            "notes": [note.model_dump() for note in tuv_model.notes]
        }
    
    def _set_tu_generic_metadata(self, tu_db_id: int, field_name: str, data: List[Dict[str, Any]], current_user_id: Optional[str]) -> Dict[str, str]:
        """
        Private helper to set generic metadata (attributes, properties, or notes) for a TU.
        The `data` is a list of dictionaries, which will be serialized to JSON by the store.
        """
        store = self._get_active_store()
        if not store.get_translation_unit_by_db_id(tu_db_id):
            raise ResourceNotFoundError(f"Translation Unit with DB ID {tu_db_id} not found.")
        
        json_data_to_store = json.dumps(data) 
        
        try:
            tu_model = store.get_translation_unit_by_db_id(tu_db_id)
            if not tu_model: raise ResourceNotFoundError(f"TU {tu_db_id} not found for metadata update.") 

            if field_name == "custom_attributes": tu_model.custom_attributes = [TMXAttribute(**d) for d in data]
            elif field_name == "properties": tu_model.properties = [TMXProperty(**p) for p in data]
            elif field_name == "notes": tu_model.notes = [TMXNote(**n) for n in data]
            else: raise ValueError(f"Invalid metadata field name: {field_name}")
            
            tu_model.change_id = current_user_id 
            success = store.update_translation_unit(tu_db_id, tu_model)

            if success:
                return {"status": "success", "message": f"TU {field_name} updated for TU DB ID {tu_db_id}."}
            else:
                raise DatabaseError(f"Failed to update TU {field_name} for TU DB ID {tu_db_id}.")
        except sqlite3.Error as e_db:
            raise DatabaseError(f"Database error updating TU {field_name} for TU DB ID {tu_db_id}.", details=str(e_db)) from e_db
        except Exception as e:
            raise TMXServerError(f"Unexpected error updating TU {field_name} for TU DB ID {tu_db_id}.", details=str(e)) from e


    def set_tu_attributes(self, tu_db_id: int, attributes_data: List[Dict[str, Any]], current_user_id: Optional[str] = None) -> Dict[str, str]:
        """Sets custom attributes for a Translation Unit."""
        return self._set_tu_generic_metadata(tu_db_id, "custom_attributes", attributes_data, current_user_id)

    def set_tu_properties(self, tu_db_id: int, properties_data: List[Dict[str, Any]], current_user_id: Optional[str] = None) -> Dict[str, str]:
        """Sets properties for a Translation Unit."""
        return self._set_tu_generic_metadata(tu_db_id, "properties", properties_data, current_user_id)

    def set_tu_notes(self, tu_db_id: int, notes_data: List[Dict[str, Any]], current_user_id: Optional[str] = None) -> Dict[str, str]:
        """Sets notes for a Translation Unit."""
        return self._set_tu_generic_metadata(tu_db_id, "notes", notes_data, current_user_id)

    def _set_tuv_generic_metadata(self, tu_db_id: int, lang_code: str, field_name: str, data: List[Dict[str, Any]], current_user_id: Optional[str]) -> Dict[str, str]:
        """
        Private helper to set generic metadata for a TUV.
        `data` is List[Dict], store method `update_tuv_field` handles serialization.
        """
        store = self._get_active_store()
        if not store.get_tuv(tu_db_id, lang_code):
            raise ResourceNotFoundError(f"TUV for TU DB ID {tu_db_id} and lang '{lang_code}' not found.")
        
        try:
            success = store.update_tuv_field(tu_db_id, lang_code, field_name, data, change_id=current_user_id) 
            if success:
                return {"status": "success", "message": f"TUV {field_name} updated for TU DB ID {tu_db_id}, lang '{lang_code}'."}
            else:
                raise DatabaseError(f"Failed to update TUV {field_name} for TU DB ID {tu_db_id}, lang '{lang_code}'. Operation reported no change.")
        except sqlite3.Error as e_db:
            raise DatabaseError(f"Database error updating TUV {field_name} for TU {tu_db_id}, lang '{lang_code}'.", details=str(e_db)) from e_db
        except Exception as e:
            raise TMXServerError(f"Unexpected error updating TUV {field_name} for TU {tu_db_id}, lang '{lang_code}'.", details=str(e)) from e


    def set_tuv_attributes(self, tu_db_id: int, lang_code: str, attributes_data: List[Dict[str, Any]], current_user_id: Optional[str] = None) -> Dict[str, str]:
        """Sets custom attributes for a Translation Unit Variant."""
        return self._set_tuv_generic_metadata(tu_db_id, lang_code, "custom_attributes", attributes_data, current_user_id)

    def set_tuv_properties(self, tu_db_id: int, lang_code: str, properties_data: List[Dict[str, Any]], current_user_id: Optional[str] = None) -> Dict[str, str]:
        """Sets properties for a Translation Unit Variant."""
        return self._set_tuv_generic_metadata(tu_db_id, lang_code, "properties", properties_data, current_user_id)

    def set_tuv_notes(self, tu_db_id: int, lang_code: str, notes_data: List[Dict[str, Any]], current_user_id: Optional[str] = None) -> Dict[str, str]:
        """Sets notes for a Translation Unit Variant."""
        return self._set_tuv_generic_metadata(tu_db_id, lang_code, "notes", notes_data, current_user_id)


    # --- TU CRUD Operations ---

    def insert_new_translation_unit(
        self, 
        tuid: Optional[str] = None, 
        variants_data: Optional[List[Dict[str, Any]]] = None, 
        properties_data: Optional[List[Dict[str, Any]]] = None,
        notes_data: Optional[List[Dict[str, Any]]] = None,
        custom_attributes_data: Optional[List[Dict[str, Any]]] = None,
        seg_type: Optional[str] = None,
        datatype: Optional[str] = None,
        usage_count: Optional[int] = None,
        current_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Inserts a new Translation Unit (TU) with its variants and metadata into the active TMX file.

        Args:
            tuid (Optional[str]): Optional TUID for the new TU.
            variants_data (Optional[List[Dict[str, Any]]]): List of dictionaries, each representing a TUV.
                Each dict must include 'lang' and 'segment_xml'. Other TUV fields are optional.
                `creation_date`, `change_date`, `creation_id`, `change_id` will be auto-populated
                if not provided. `segment_text_pure` is derived from `segment_xml`.
            properties_data (Optional[List[Dict[str, Any]]]): Data for TU-level TMXProperty objects.
            notes_data (Optional[List[Dict[str, Any]]]): Data for TU-level TMXNote objects.
            custom_attributes_data (Optional[List[Dict[str, Any]]]): Data for TU-level TMXAttribute objects.
            seg_type (Optional[str]): Segment type for the TU.
            datatype (Optional[str]): Datatype for the TU.
            usage_count (Optional[int]): Usage count for the TU.
            current_user_id (Optional[str]): Identifier for the user creating the TU.
                                           Used for `creation_id` and `change_id` if not in `variants_data`.

        Returns:
            Dict[str, Any]: A dictionary representation of the newly created and retrieved TranslationUnit model.

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If `variants_data` is missing, empty, or a variant lacks required fields.
            DatabaseError: If database insertion or subsequent retrieval fails.
        """
        tmx_id = self._ensure_active_tmx_file_id()
        store = self._get_active_store()

        if not variants_data: 
            raise InvalidOperationError("Cannot insert a translation unit with no variants_data provided.")
            
        parsed_variants: List[TranslationUnitVariant] = []
        for var_dict in variants_data:
            if "segment_xml" not in var_dict:
                raise InvalidOperationError("Each variant in variants_data must have 'segment_xml'.")
            if "lang" not in var_dict:
                 raise InvalidOperationError("Each variant in variants_data must have 'lang'.")

            var_dict["segment_text_pure"] = extract_pure_text_from_segment_xml(var_dict["segment_xml"])
            var_dict.setdefault("creation_id", current_user_id)
            var_dict.setdefault("change_id", current_user_id) # For new TUV, change_id can be same as creation_id
            var_dict.setdefault("creation_date", datetime.now(timezone.utc))
            var_dict.setdefault("change_date", datetime.now(timezone.utc)) # For new TUV, change_date is creation_date
            parsed_variants.append(TranslationUnitVariant(**var_dict))
        
        if not parsed_variants: 
            raise InvalidOperationError("Cannot insert a translation unit with no valid variants processed.")

        tu_creation_date = datetime.now(timezone.utc)
        new_tu_model = TranslationUnit(
            tuid=tuid,
            variants=parsed_variants,
            properties=[TMXProperty(**p) for p in (properties_data or [])],
            notes=[TMXNote(**n) for n in (notes_data or [])],
            custom_attributes=[TMXAttribute(**ca) for ca in (custom_attributes_data or [])],
            seg_type=seg_type,
            datatype=datatype,
            usage_count=usage_count,
            creation_date=tu_creation_date,
            change_date=tu_creation_date, 
            creation_id=current_user_id,
            change_id=current_user_id
        )
        
        tu_db_id = store.add_translation_unit(new_tu_model, tmx_id, position=None) 

        created_tu_model = store.get_translation_unit_by_db_id(tu_db_id)
        if not created_tu_model:
            raise DatabaseError(f"Failed to retrieve newly inserted TU with DB ID {tu_db_id} after creation.")
        
        return created_tu_model.model_dump(exclude_none=True) 

    def delete_translation_unit_by_id(self, tu_db_id: int) -> Dict[str, str]:
        """
        Deletes a Translation Unit (TU) by its unique database ID.
        This operation will also delete all associated Translation Unit Variants (TUVs)
        due to database cascade rules defined in the schema.

        Args:
            tu_db_id (int): The database ID of the TU to be deleted.

        Returns:
            Dict[str, str]: A confirmation message of the deletion.

        Raises:
            SessionInactiveError: If no TMX session is active.
            ResourceNotFoundError: If the TU with the specified `tu_db_id` is not found
                                   (i.e., no rows were deleted).
            DatabaseError: If any other database error occurs during the deletion process.
        """
        store = self._get_active_store() 
        if store.delete_translation_unit(tu_db_id):
            return {"status": "success", "message": f"Translation Unit with DB ID {tu_db_id} deleted."}
        else:
            raise ResourceNotFoundError(f"Translation Unit with DB ID {tu_db_id} not found.")


    def get_segments(
        self,
        filter_text: Optional[str] = None,
        filter_lang_code: Optional[str] = None,
        case_sensitive_filter: bool = False,
        is_regex_filter: bool = False,
        filter_untranslated_to_other_langs: bool = False,
        source_lang_for_untranslated_filter: Optional[str] = None,
        sort_by_text_in_lang_code: Optional[str] = None,
        sort_ascending: bool = True,
        start_offset: int = 0,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        Retrieves Translation Units (TUs/segments) based on a comprehensive set of
        filtering, sorting, and pagination criteria.

        Args:
            filter_text (Optional[str]): Text to search for within segment content (`segment_text_pure`).
            filter_lang_code (Optional[str]): Language code to apply `filter_text` to.
                                            Must be provided if `filter_text` is present.
            case_sensitive_filter (bool): If True, `filter_text` search is case-sensitive.
                                          Defaults to False.
            is_regex_filter (bool): If True, `filter_text` is treated as a regex pattern.
                                    Defaults to False.
            filter_untranslated_to_other_langs (bool): If True, filters for TUs that have a
                non-empty source segment (in `source_lang_for_untranslated_filter`) but are
                untranslated (missing or empty `segment_text_pure`) in all *other* languages.
                Defaults to False.
            source_lang_for_untranslated_filter (Optional[str]): The source language to use for
                the `filter_untranslated_to_other_langs` logic. Required if that filter is True.
            sort_by_text_in_lang_code (Optional[str]): Language code whose `segment_text_pure`
                should be used for sorting the results.
            sort_ascending (bool): True for ascending sort, False for descending. Defaults to True.
            start_offset (int): The starting offset for pagination (0-indexed). Defaults to 0.
            page_size (int): The number of TUs to return per page. Use -1 or 0 for no limit
                             (fetches all matching TUs). Defaults to 20.

        Returns:
            Dict[str, Any]: A dictionary containing:
                            - "status" (str): "success".
                            - "units" (List[Dict]): A list of TU data (Pydantic models dumped to dicts).
                            - "total_count" (int): Total number of TUs matching the filters (before pagination).
                            - "offset" (int): The `start_offset` used.
                            - "limit" (int): The `page_size` used (or `total_count` if no limit).

        Raises:
            SessionInactiveError: If no TMX session is active.
            InvalidOperationError: If filter parameters are invalid (e.g., `filter_text`
                                   is provided without `filter_lang_code`, or
                                   `filter_untranslated_to_other_langs` is True without
                                   `source_lang_for_untranslated_filter`).
            DatabaseError: If a database error occurs during retrieval.
        """
        if filter_text and not filter_lang_code:
            raise InvalidOperationError("If 'filter_text' is provided, 'filter_lang_code' must also be specified.")
        if filter_untranslated_to_other_langs and not source_lang_for_untranslated_filter:
            raise InvalidOperationError("If 'filter_untranslated_to_other_langs' is True, 'source_lang_for_untranslated_filter' must be specified.")

        store = self._get_active_store()
        tmx_id = self._ensure_active_tmx_file_id()

        if page_size < 0: 
            page_size = -1 
        if start_offset < 0:
            start_offset = 0

        tus, total_count = store.get_translation_units_filtered(
            tmx_file_id=tmx_id,
            filter_text=filter_text,
            filter_lang_code=filter_lang_code,
            case_sensitive_filter=case_sensitive_filter,
            is_regex_filter=is_regex_filter,
            filter_untranslated_to_other_langs=filter_untranslated_to_other_langs,
            source_lang_for_untranslated_filter=source_lang_for_untranslated_filter,
            sort_by_text_in_lang_code=sort_by_text_in_lang_code,
            sort_ascending=sort_ascending,
            start_offset=start_offset,
            page_size=page_size
        )

        return {
            "status": "success", 
            "units": [tu.model_dump(exclude_none=True) for tu in tus], 
            "total_count": total_count,
            "offset": start_offset,
            "limit": page_size if page_size > 0 else total_count 
        }
        
# Example of TMXConverter (from Chunk 6, for context, not for this chunk's execution)
# class TMXConverter:
#     def to_alternative_format(self, data): # Placeholder
#         pass
#     def from_alternative_format(self, data): # Placeholder
#         pass

if __name__ == '__main__':
    # This section provides basic, illustrative command-line tests for TMXService.
    # It is not a comprehensive test suite.
    # import re # Already imported globally
    # import sqlite3 # Already imported globally
    # from datetime import datetime # Already imported globally

    dummy_tmx_content = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <header creationtool="TestTool" creationtoolversion="1.0" segtype="sentence" o-tmf="unknown" adminlang="en" srclang="en-US" datatype="plaintext" creationdate="20230101T120000Z"/>
  <body>
    <tu tuid="1">
      <tuv xml:lang="en-US"><seg>Hello world.</seg></tuv>
      <tuv xml:lang="fr-FR"><seg>Bonjour le monde.</seg></tuv>
      <tuv xml:lang="es-ES"><seg>Hola mundo.</seg></tuv>
    </tu>
    <tu tuid="2">
      <prop type="x-category">General</prop>
      <tuv xml:lang="en-US"><seg>Another sentence.</seg></tuv>
      <tuv xml:lang="fr-FR"><seg>Une autre phrase.</seg></tuv>
    </tu>
  </body>
</tmx>
"""
    dummy_tmx_filepath = "dummy_chunk5_test.tmx"
    with open(dummy_tmx_filepath, "w", encoding="utf-8") as f:
        f.write(dummy_tmx_content)

    output_tmx_filepath = "dummy_chunk5_output.tmx" 
    test_db_path = "test_service_chunk5.db"

    service = TMXService(db_path=test_db_path)

    try:
        print(f"--- Opening file: {dummy_tmx_filepath} ---")
        service.open_file(dummy_tmx_filepath)
        print("File opened successfully.")
        
        info_after_open = service.get_file_info()
        assert info_after_open.is_active
        assert info_after_open.header is not None
        print(f"Initial languages: {info_after_open.language_codes}")
        assert "es-ES" in info_after_open.language_codes

        print("\n--- Testing add_language ---")
        add_lang_result = service.add_language("de-DE")
        print(f"add_language('de-DE') result: {add_lang_result}")
        assert add_lang_result["status"] == "success"
        try:
            service.add_language("invalid code") 
        except InvalidOperationError as e: 
            print(f"Caught expected error for invalid lang format: {e}")
        
        print("\n--- Testing remove_language ---")
        remove_result = service.remove_language("es-ES")
        print(f"remove_language('es-ES') result: {remove_result}")
        assert remove_result["status"] == "success"
        info_after_remove = service.get_file_info()
        print(f"Languages after remove: {info_after_remove.language_codes}")
        assert "es-ES" not in info_after_remove.language_codes

        print("\n--- Testing change_language_code ---")
        change_result = service.change_language_code("fr-FR", "fr-CA", "test_user_id")
        print(f"change_language_code('fr-FR' to 'fr-CA') result: {change_result}")
        assert change_result["status"] == "success"
        info_after_change = service.get_file_info()
        print(f"Languages after change: {info_after_change.language_codes}")
        assert "fr-FR" not in info_after_change.language_codes
        assert "fr-CA" in info_after_change.language_codes
        
        try:
            service.change_language_code("en-US", "fr-CA") 
        except InvalidOperationError as e:
            print(f"Caught expected error for changing to existing lang: {e}")

        print("\n--- Testing set_source_language ---")
        if info_after_open.header : 
            assert info_after_open.header.srclang == "en-US" 
        set_src_result = service.set_source_language("fr-CA", "test_user_id")
        print(f"set_source_language('fr-CA') result: {set_src_result}")
        assert set_src_result["status"] == "success"
        info_after_set_src = service.get_file_info()
        assert info_after_set_src.header is not None
        assert info_after_set_src.header.srclang == "fr-CA"
        print(f"Source language after set: {info_after_set_src.header.srclang}")

    except Exception as e:
        print(f"Error during TMXService Chunk 5 test: {type(e).__name__} - {e}")
    finally:
        print("\n--- Closing file (Chunk 5 test block) ---")
        service.close_file()
        if os.path.exists(dummy_tmx_filepath): os.remove(dummy_tmx_filepath)
        if os.path.exists(output_tmx_filepath): os.remove(output_tmx_filepath)
        if os.path.exists(test_db_path): os.remove(test_db_path)
        print("Chunk 5 test files cleaned up.")

    # Test block for general service functionality (open, save, info)
    dummy_tmx_filepath_general = "dummy_test_input_general.tmx"
    with open(dummy_tmx_filepath_general, "w", encoding="utf-8") as f:
        f.write(dummy_tmx_content) 

    output_tmx_filepath_general = "dummy_test_output_general.tmx"
    test_db_path_general = "test_service_general.db"
    service_general = TMXService(db_path=test_db_path_general)

    try:
        print(f"\n--- General Service Test: Initial File Info ---")
        info = service_general.get_file_info()
        print(info.model_dump_json(indent=2))
        assert not info.is_active

        print(f"\n--- General Service Test: Opening file: {dummy_tmx_filepath_general} ---")
        service_general.open_file(dummy_tmx_filepath_general)
        print("File opened successfully.")
        
        info_after_open_general = service_general.get_file_info()
        print(f"\n--- General Service Test: File Info After Open ---")
        print(info_after_open_general.model_dump_json(indent=2))
        assert info_after_open_general.is_active
        assert info_after_open_general.file_path == dummy_tmx_filepath_general
        if info_after_open_general.header: 
             assert info_after_open_general.header.srclang == "en-US"
        assert info_after_open_general.tu_count == 2
        assert sorted(info_after_open_general.language_codes) == sorted(["en-US", "es-ES", "fr-FR"]) 

        print(f"\n--- General Service Test: Saving file to: {output_tmx_filepath_general} ---")
        service_general.save_file(output_tmx_filepath_general)
        print(f"File saved to {output_tmx_filepath_general}")
        assert os.path.exists(output_tmx_filepath_general)

        with open(output_tmx_filepath_general, "r", encoding="utf-8") as f_out:
            saved_content = f_out.read()
            assert "<seg>Hello world.</seg>" in saved_content
            if info_after_open_general.header: 
                 assert f'srclang="{info_after_open_general.header.srclang}"' in saved_content

    except Exception as e:
        print(f"Error during TMXService general test: {type(e).__name__} - {e}")
    finally:
        print("\n--- General Service Test: Closing file ---")
        service_general.close_file()
        info_after_close_general = service_general.get_file_info()
        print(f"\n--- General Service Test: File Info After Close ---")
        print(info_after_close_general.model_dump_json(indent=2))
        assert not info_after_close_general.is_active
        
        if os.path.exists(dummy_tmx_filepath_general): os.remove(dummy_tmx_filepath_general)
        if os.path.exists(output_tmx_filepath_general): os.remove(output_tmx_filepath_general)
        if os.path.exists(test_db_path_general): os.remove(test_db_path_general)
        print("General test files cleaned up.")

    print("\n--- Testing extract_pure_text_from_segment_xml (already tested, confirming) ---")
    xml_sample1 = "<seg>This is <b>bold</b> and <i>italic</i> text.</seg>"
    assert extract_pure_text_from_segment_xml(xml_sample1) == "This is bold and italic text."
    print("extract_pure_text_from_segment_xml tests (re-confirmation) passed.")
