import csv
import openpyxl # For Excel processing
from lxml import etree # For creating XML segments
from datetime import datetime, timezone
import os
from typing import List, Tuple, Optional, Dict, Any

from .models import TMXHeader, TranslationUnit, TranslationUnitVariant, TMXProperty, TMXNote, TMXAttribute
from .storage import SQLiteStore # For tmx_content_to_csv/excel methods

class TMXConverter:
    """
    Handles conversion between TMX format and other data formats like CSV and Excel.
    """

    def _create_segment_xml_str(self, text: Optional[str]) -> str:
        """
        Creates a simple <seg>text</seg> XML string.
        Handles None input for text by creating an empty segment.
        Uses lxml to ensure proper XML character escaping if text_content has special chars.
        """
        seg_element = etree.Element("seg")
        if text is not None:
            seg_element.text = str(text) # Ensure text is a string
        return etree.tostring(seg_element, encoding="unicode")

    def csv_to_tmx_content(
        self,
        csv_filepath: str,
        languages: List[str], # List of language codes corresponding to CSV columns after potential TUID column
        charset: str = 'utf-8',
        delimiter: str = ',',
        quotechar: str = '"',
        tuid_col_index: Optional[int] = None # Optional index for a column containing TUIDs
    ) -> Tuple[TMXHeader, List[TranslationUnit]]:
        """
        Converts data from a CSV file into TMX Pydantic models (Header and TranslationUnits).

        Args:
            csv_filepath: Path to the input CSV file.
            languages: List of language codes corresponding to CSV columns,
                       ordered as they appear after the optional TUID column.
            charset: Character set of the CSV file.
            delimiter: Delimiter used in the CSV file.
            quotechar: Quote character used in the CSV file.
            tuid_col_index: Optional 0-based index of the column containing TUIDs.
                            If None, TUIDs are auto-generated.

        Returns:
            A tuple containing the generated TMXHeader and a list of TranslationUnit models.
        
        Raises:
            FileNotFoundError: If the csv_filepath does not exist.
            ValueError: If the languages list is empty.
            RuntimeError: For errors during CSV processing.
        """
        if not os.path.exists(csv_filepath):
            raise FileNotFoundError(f"CSV file not found: {csv_filepath}")
        if not languages:
            raise ValueError("Language list cannot be empty.")

        translation_units: List[TranslationUnit] = []
        
        # Create a basic TMXHeader
        # Use the first language as srclang if not otherwise specified
        header = TMXHeader(
            creation_tool="TMXEditorServer-CSVConverter",
            creation_tool_version="0.1.0", # Replace with actual version later
            seg_type="sentence", # Default, can be configured
            o_tmf="CSV", # Original TMX Format (arbitrary for converted files)
            admin_lang="en", # Default admin language
            src_lang=languages[0], 
            datatype="plaintext", # Assuming plaintext from CSV
            creation_date=datetime.now(timezone.utc)
        )

        try:
            with open(csv_filepath, mode='r', encoding=charset, newline='') as csvfile:
                reader = csv.reader(csvfile, delimiter=delimiter, quotechar=quotechar)
                
                # Skip header row if it's just language codes (or handle it to map columns)
                # For now, assumes languages list order matches CSV columns (after TUID col if present)
                
                current_datetime = datetime.now(timezone.utc)
                for i, row in enumerate(reader):
                    if not any(field.strip() for field in row): # Skip empty rows
                        continue

                    tuid_val: Optional[str] = None
                    lang_col_offset = 0
                    if tuid_col_index is not None and 0 <= tuid_col_index < len(row):
                        tuid_val = row[tuid_col_index].strip()
                        lang_col_offset = 1 # If TUID is present, language columns might be shifted
                    
                    if not tuid_val: # Auto-generate TUID if not provided or empty
                        tuid_val = f"csv_tu_{i+1}"

                    variants: List[TranslationUnitVariant] = []
                    for lang_idx, lang_code in enumerate(languages):
                        actual_col_idx = (tuid_col_index + lang_col_offset + lang_idx) if tuid_col_index is not None else lang_idx

                        if actual_col_idx < len(row):
                            segment_text = row[actual_col_idx].strip()
                            variants.append(TranslationUnitVariant(
                                lang=lang_code,
                                segment_xml=self._create_segment_xml_str(segment_text),
                                segment_text_pure=segment_text,
                                creation_date=current_datetime,
                                change_date=current_datetime,
                            ))
                        else: # Handle rows with fewer columns than expected languages (add empty TUV)
                             variants.append(TranslationUnitVariant(
                                lang=lang_code,
                                segment_xml=self._create_segment_xml_str(""),
                                segment_text_pure="",
                                creation_date=current_datetime,
                                change_date=current_datetime,
                            ))


                    if variants: # Only add TU if it has at least one variant
                        tu = TranslationUnit(
                            tuid=tuid_val,
                            variants=variants,
                            creation_date=current_datetime,
                            change_date=current_datetime,
                            # srclang can be set to the first language in the CSV for this TU, or from header
                            srclang=languages[0] if languages else None 
                        )
                        translation_units.append(tu)
            
            return header, translation_units
        except Exception as e:
            raise RuntimeError(f"Error processing CSV file '{csv_filepath}': {e}")


    def excel_to_tmx_content(
        self,
        excel_filepath: str,
        languages: List[str], # List of language codes corresponding to Excel columns after TUID
        sheet_name: Optional[str] = None, # If None, uses the first active sheet
        header_row_index: Optional[int] = 1, # 1-based index of the row containing language headers (if any)
        data_start_row_index: int = 2, # 1-based index for where data rows start
        tuid_col_letter: Optional[str] = None # e.g., 'A' for TUID column
    ) -> Tuple[TMXHeader, List[TranslationUnit]]:
        """
        Converts data from an Excel file into TMX Pydantic models (Header and TranslationUnits).

        Args:
            excel_filepath: Path to the input Excel file.
            languages: List of language codes. Their order is used to map to columns
                       after the optional TUID column, or as per header_row_index.
            sheet_name: Optional name of the Excel sheet to import. If None, the first active sheet is used.
            header_row_index: Optional 1-based index of the row containing language headers.
                              If provided, used to map languages to columns. Otherwise, `languages` list order is critical.
            data_start_row_index: 1-based index from where the actual data rows start.
            tuid_col_letter: Optional column letter (e.g., 'A', 'B') for TUIDs.
                             If None, TUIDs are auto-generated.
        
        Returns:
            A tuple containing the generated TMXHeader and a list of TranslationUnit models.

        Raises:
            FileNotFoundError: If the excel_filepath does not exist.
            ValueError: If the languages list is empty or sheet is not found.
            RuntimeError: For errors during Excel processing.
        """
        if not os.path.exists(excel_filepath):
            raise FileNotFoundError(f"Excel file not found: {excel_filepath}")
        if not languages:
            raise ValueError("Language list cannot be empty.")

        translation_units: List[TranslationUnit] = []
        header = TMXHeader(
            creation_tool="TMXEditorServer-ExcelConverter",
            creation_tool_version="0.1.0",
            seg_type="sentence", admin_lang="en", src_lang=languages[0], datatype="plaintext",
            creation_date=datetime.now(timezone.utc), o_tmf="Excel"
        )

        try:
            workbook = openpyxl.load_workbook(excel_filepath, read_only=True)
            sheet = workbook[sheet_name] if sheet_name else workbook.active
            if not sheet: raise ValueError("Sheet not found or workbook empty.")

            # If header_row_index is provided, could potentially map languages to columns by reading headers
            # For now, assumes `languages` list order matches columns after `tuid_col_letter`

            current_datetime = datetime.now(timezone.utc)
            for i, row_cells in enumerate(sheet.iter_rows(min_row=data_start_row_index)):
                if not any(cell.value for cell in row_cells): continue # Skip empty rows

                row_values = [str(cell.value).strip() if cell.value is not None else "" for cell in row_cells]
                
                tuid_val: Optional[str] = None
                lang_col_start_index = 0 # 0-based index in row_values

                if tuid_col_letter:
                    tuid_col_idx_0_based = openpyxl.utils.column_index_from_string(tuid_col_letter.upper()) - 1
                    if 0 <= tuid_col_idx_0_based < len(row_values):
                        tuid_val = row_values[tuid_col_idx_0_based]
                        lang_col_start_index = tuid_col_idx_0_based + 1
                
                if not tuid_val: tuid_val = f"excel_tu_{i+1}"

                variants: List[TranslationUnitVariant] = []
                for lang_idx, lang_code in enumerate(languages):
                    actual_col_idx_in_row_values = lang_col_start_index + lang_idx
                    segment_text = ""
                    if actual_col_idx_in_row_values < len(row_values):
                        segment_text = row_values[actual_col_idx_in_row_values]
                    
                    variants.append(TranslationUnitVariant(
                        lang=lang_code,
                        segment_xml=self._create_segment_xml_str(segment_text),
                        segment_text_pure=segment_text,
                        creation_date=current_datetime, change_date=current_datetime
                    ))
                
                if variants:
                    translation_units.append(TranslationUnit(
                        tuid=tuid_val, variants=variants, creation_date=current_datetime,
                        change_date=current_datetime, srclang=languages[0] if languages else None
                    ))
            
            return header, translation_units
        except Exception as e:
            raise RuntimeError(f"Error processing Excel file '{excel_filepath}': {e}")


    def tmx_content_to_csv(
        self, store: SQLiteStore, tmx_file_id: int, csv_filepath: str, 
        charset: str = 'utf-8', delimiter: str = ','
    ) -> None:
        """
        Exports TMX data from the SQLiteStore to a CSV file.

        Args:
            store: The SQLiteStore instance containing the TMX data.
            tmx_file_id: The ID of the TMX file session in the store.
            csv_filepath: Path to save the output CSV file.
            charset: Character set for the output CSV file.
            delimiter: Delimiter to use in the CSV file.
        
        Raises:
            ValueError: If the header for the TMX file is not found in the store.
            RuntimeError: For errors during CSV file writing.
        """
        header_model = store.get_header(tmx_file_id)
        if not header_model: raise ValueError("Header not found for TMX file.")
        
        # Get all TUs and all language codes present in the file
        all_tus = store.get_all_translation_units(tmx_file_id, limit=-1) # Get all
        lang_codes = store.get_language_codes(tmx_file_id)
        if not lang_codes: # If no TUVs, use header languages as fallback or error
            lang_codes = [header_model.srclang] if header_model.srclang else ["unknown_lang"]


        try:
            with open(csv_filepath, mode='w', encoding=charset, newline='') as csvfile:
                writer = csv.writer(csvfile, delimiter=delimiter)
                
                # Write header row: TUID + language codes
                csv_header = ["TUID"] + lang_codes
                writer.writerow(csv_header)

                for tu in all_tus:
                    row_data = [tu.tuid or ""]
                    # Create a dictionary of lang -> text_pure for quick lookup
                    tuv_texts: Dict[str, str] = {v.lang: (v.segment_text_pure or "") for v in tu.variants}
                    for lang_code in lang_codes:
                        row_data.append(tuv_texts.get(lang_code, "")) # Append text or empty string if lang not in this TU
                    writer.writerow(row_data)
        except Exception as e:
            raise RuntimeError(f"Error exporting TMX to CSV '{csv_filepath}': {e}")


    def tmx_content_to_excel(
        self, store: SQLiteStore, tmx_file_id: int, excel_filepath: str
    ) -> None:
        """
        Exports TMX data from the SQLiteStore to an Excel (XLSX) file.

        Args:
            store: The SQLiteStore instance containing the TMX data.
            tmx_file_id: The ID of the TMX file session in the store.
            excel_filepath: Path to save the output Excel file.

        Raises:
            ValueError: If the header for the TMX file is not found in the store.
            RuntimeError: For errors during Excel file writing or if the active sheet cannot be accessed.
        """
        header_model = store.get_header(tmx_file_id)
        if not header_model: raise ValueError("Header not found for TMX file.")

        all_tus = store.get_all_translation_units(tmx_file_id, limit=-1)
        lang_codes = store.get_language_codes(tmx_file_id)
        if not lang_codes:
             lang_codes = [header_model.srclang] if header_model.srclang else ["unknown_lang"]

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        if not sheet: # Should not happen for a new workbook
            raise RuntimeError("Could not get active sheet from new workbook.")
        sheet.title = "TMX Data"

        # Write header row
        excel_header = ["TUID"] + lang_codes
        sheet.append(excel_header)

        for tu in all_tus:
            row_data = [tu.tuid or ""]
            tuv_texts: Dict[str, str] = {v.lang: (v.segment_text_pure or "") for v in tu.variants}
            for lang_code in lang_codes:
                row_data.append(tuv_texts.get(lang_code, ""))
            sheet.append(row_data)
        
        try:
            workbook.save(excel_filepath)
        except Exception as e:
            raise RuntimeError(f"Error exporting TMX to Excel '{excel_filepath}': {e}")

if __name__ == '__main__':
    # Basic test requires a dummy SQLiteStore and populating it, or mock it.
    # For now, this block can test individual helper methods if any, or simple file presence.
    print("TMXConverter class defined.")
    converter = TMXConverter()
    xml_seg = converter._create_segment_xml_str("Hello & <world>!")
    print(f"Generated segment: {xml_seg}") # Expected: <seg>Hello &amp; &lt;world&gt;!</seg>
    assert xml_seg == "<seg>Hello &amp; &lt;world&gt;!</seg>"
    
    xml_empty_seg = converter._create_segment_xml_str(None)
    print(f"Generated empty segment: {xml_empty_seg}")
    assert xml_empty_seg == "<seg></seg>"

    # Further tests would require creating dummy CSV/Excel files and a test DB.
    # Example:
    # test_csv_path = "test_input.csv"
    # with open(test_csv_path, "w", newline="") as f:
    #     writer = csv.writer(f)
    #     writer.writerow(["ID", "en-US", "fr-FR"])
    #     writer.writerow(["tu1", "Hello", "Bonjour"])
    #     writer.writerow(["tu2", "World", "Monde"])
    # try:
    #     header, tus = converter.csv_to_tmx_content(test_csv_path, languages=["en-US", "fr-FR"], tuid_col_index=0)
    #     print(f"CSV Conversion: Header srclang={header.srclang}, TU count={len(tus)}")
    #     assert len(tus) == 2
    #     assert tus[0].variants[0].segment_text_pure == "Hello"
    # finally:
    #     if os.path.exists(test_csv_path): os.remove(test_csv_path)
    print("TMXConverter basic tests passed.")
