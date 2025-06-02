"""
Parses and writes TMX (Translation Memory eXchange) files according to TMX 1.4 specification.
This module provides the TMXParser class for handling the reading and writing of TMX
documents, converting them to and from Pydantic models defined in `models.py`.
It also includes utility functions for TMX-specific data conversions like datetime
formatting and extraction of plain text from XML segments.
"""
from lxml import etree
from typing import List, Tuple, Iterable, Optional, Dict, Any
from datetime import datetime
# Consider adding logging for warnings/errors instead of print in a production environment.
# import logging
# logger = logging.getLogger(__name__)

from .models import (
    TMXHeader,
    TranslationUnit,
    TranslationUnitVariant,
    TMXProperty,
    TMXNote,
    TMXAttribute
)

# Standard TMX attributes for header, tu, tuv that are explicitly defined in models
# Others will be captured in custom_attributes
KNOWN_HEADER_ATTRS = {
    "creationtool", "creationtoolversion", "segtype", "o-tmf", 
    "adminlang", "srclang", "datatype", "o-encoding", 
    "creationdate", "creationid", "changedate", "changeid"
}
KNOWN_TU_ATTRS = {
    "tuid", "usagecount", "lastusagedate", "creationdate", "creationid",
    "changedate", "changeid", "segtype", "datatype", "srclang"
}
KNOWN_TUV_ATTRS = {
    "xml:lang", "creationdate", "creationid", "changedate", "changeid",
    "lastusagedate", "usagecount"
}

def _parse_datetime_tmx(value: Optional[str]) -> Optional[datetime]:
    """
    Parses TMX datetime strings.
    TMX spec suggests YYYYMMDDThhmmssZ or YYYYMMDD.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y%m%dT%H%M%SZ')
    except ValueError:
        try:
            # Attempt to parse just the date part if time is not included
            return datetime.strptime(value, '%Y%m%d')
        except ValueError:
            # print(f"Warning: Could not parse datetime string: {value}") # Replaced by raising error or logging
            # For robustness, this could raise a custom parsing error or log.
            # Returning None implies the date string was invalid or ignorable.
            return None

def _datetime_to_tmx_str(dt_obj: Optional[datetime]) -> Optional[str]:
    """Converts a datetime object to a TMX-compliant datetime string (YYYYMMDDThhmmssZ)."""
    if not dt_obj:
        return None
    return dt_obj.strftime('%Y%m%dT%H%M%SZ')

def extract_pure_text_from_segment_xml(segment_xml: str) -> str:
    """
    Extracts and returns the concatenated text content from an XML segment string.
    e.g., "<seg>This is <b>bold</b> text.</seg>" -> "This is bold text."
    """
    if not segment_xml or not segment_xml.strip():
        return ""
    try:
        # Wrap with a root element if segment_xml is just the content of <seg>
        # If segment_xml is a full <seg>...</seg> string, etree.fromstring should handle it.
        # Make sure to handle cases where segment_xml might be just text, not a full <seg> element.
        # The string might not be a valid XML doc on its own if it's just inner content.
        # A common pattern is to wrap it if it doesn't start with '<'.
        # However, TMX <seg> content is defined as "text and/or code elements".
        # If it's already a <seg>...</seg> string, fromstring will parse it.
        # If it's inner content like "text <b>bold</b>", fromstring might fail.
        # The expectation from TUV model is that segment_xml is the *full* <seg>...</seg> element string.

        parser = etree.XMLParser(recover=True) 
        # Assuming segment_xml is a string like "<seg>content</seg>"
        # If segment_xml is just "content", fromstring will fail.
        # The function in services.py seemed to assume segment_xml is the full tag.
        xml_element = etree.fromstring(segment_xml, parser=parser)
        
        text_content = "".join(xml_element.itertext())
        return text_content.strip()
    except etree.XMLSyntaxError:
        # If parsing fails, it might be plain text without tags or malformed.
        # Fallback: try to return the string as is, assuming it might be plain text.
        # This matches the previous behavior of printing a warning and returning ""
        # but returning the original text might be more useful if it was just plain text.
        # For consistency with the original function's fallback (empty string), let's keep it.
        # However, a better fallback for "plain text" might be to return segment_xml.strip() directly.
        # Let's refine this: if it's simple text, it might not be an error.
        # The original function printed a warning and returned "".
        # A robust solution might involve checking if the string looks like XML first.
        # For now, let's keep the simple try-except-return-empty, as per original function's behavior.
        return "" # Fallback for malformed XML or non-XML text.
    except Exception: 
        # Catch any other unexpected errors during parsing.
        return ""


class TMXParser:
    """
    Handles parsing of TMX 1.4 files into Pydantic models and writing these models back to TMX files.
    It uses `lxml` for efficient XML processing.
    """

    def _parse_common_elements(self, element: etree._Element) -> Tuple[List[TMXProperty], List[TMXNote], Dict[str, Any]]:
        """
        Helper to parse <prop> and <note> child elements, and extract all raw attributes from a given TMX element.
        
        Args:
            element: The lxml element (header, tu, or tuv) to parse.

        Returns:
            A tuple containing:
                - A list of TMXProperty models.
                - A list of TMXNote models.
                - A dictionary of the element's raw attributes.
        """
        properties = []
        notes = []
        # custom_attributes_list is handled by _populate_model_attrs from raw_attrs
        
        raw_attrs = dict(element.attrib)
        # model_attrs is not built here, but rather in _populate_model_attrs

        for child in element:
            if child.tag == "prop":
                prop_data = {"type": child.get("type"), "value": child.text}
                if child.get("{http://www.w3.org/XML/1998/namespace}lang"):
                    prop_data["lang"] = child.get("{http://www.w3.org/XML/1998/namespace}lang")
                properties.append(TMXProperty(**prop_data))
            elif child.tag == "note":
                note_data = {"text": child.text}
                if child.get("{http://www.w3.org/XML/1998/namespace}lang"):
                    note_data["lang"] = child.get("{http://www.w3.org/XML/1998/namespace}lang")
                if child.get("o-encoding"):
                    note_data["o_encoding"] = child.get("o-encoding")
                notes.append(TMXNote(**note_data))
        
        return properties, notes, raw_attrs
    
    def _populate_model_attrs(self, raw_attrs: Dict[str, Any], known_attrs_set: set) -> Tuple[Dict[str, Any], List[TMXAttribute]]:
        """
        Separates known attributes (for direct model field population) from custom attributes.
        Handles type conversions for specific known attributes like datetimes and integers.

        Args:
            raw_attrs: Dictionary of raw attributes from an XML element.
            known_attrs_set: A set of attribute names that are known fields in the target Pydantic model.

        Returns:
            A tuple containing:
                - A dictionary of attributes to be used for Pydantic model construction (known fields).
                - A list of TMXAttribute models for custom attributes.
        """
        model_constructor_attrs = {}
        custom_attributes_list = []

        for name, value in raw_attrs.items():
            # Normalize attribute names for Pydantic model fields (e.g., xml:lang -> lang)
            pydantic_name = name.replace("xml:", "").replace("o-", "o_")
            
            if pydantic_name in known_attrs_set or name in known_attrs_set : # Check both original and pydantic_name
                # Handle datetime conversions
                if pydantic_name in ["creationdate", "changedate", "lastusagedate"]:
                    model_constructor_attrs[pydantic_name] = _parse_datetime_tmx(value)
                elif name in ["creationdate", "changedate", "lastusagedate"]: # Original names
                     model_constructor_attrs[name] = _parse_datetime_tmx(value)
                elif pydantic_name == "usagecount":
                    model_constructor_attrs[pydantic_name] = int(value)
                else:
                    model_constructor_attrs[pydantic_name] = value
            else:
                custom_attributes_list.append(TMXAttribute(name=name, value=value))
        return model_constructor_attrs, custom_attributes_list


    def parse_tmx_file(self, filepath: str) -> Tuple[TMXHeader, List[TranslationUnit]]:
        """
        Parses a TMX file and returns TMXHeader and a list of TranslationUnit models.
        Uses iterparse for memory efficiency with large files.

        Args:
            filepath: Path to the TMX file.

        Returns:
            A tuple containing the parsed TMXHeader model and a list of TranslationUnit models.

        Raises:
            ValueError: If the TMX file is not valid (e.g., missing <header> or <tmx> root).
                        Also raised by lxml's etree.XMLSyntaxError for invalid XML.
            RuntimeError: For other unexpected errors during parsing. 
                          (Note: Service layer maps these to TMXParsingError or TMXServerError)
        """
        # Note: `print` statements within this method are for basic diagnostics during development.
        # In a production environment, these should be replaced with a proper logging mechanism.
        try:
            context = etree.iterparse(filepath, events=("start", "end"), recover=True)
            context = iter(context) # make it an iterator

            header_data = {}
            header_properties = []
            header_notes = []
            header_custom_attrs = []
            
            translation_units = []
            current_tu_data = None
            current_tu_props = []
            current_tu_notes = []
            current_tu_custom_attrs_list = []
            current_tu_variants = []
            
            current_tuv_data = None
            current_tuv_props = []
            current_tuv_notes = []
            current_tuv_custom_attrs_list = []

            # Find root tmx element first to ensure it's a TMX file (optional check)
            event, root = next(context)
            if not (event == "start" and root.tag == "tmx"):
                 raise ValueError("Not a valid TMX file (missing <tmx> root element).")

            for event, elem in context:
                if event == "start":
                    if elem.tag == "header":
                        header_props_parsed, header_notes_parsed, _, header_raw_attrs = self._parse_common_elements(elem)
                        header_properties.extend(header_props_parsed)
                        header_notes.extend(header_notes_parsed)
                        
                        model_attrs, custom_attrs = self._populate_model_attrs(header_raw_attrs, KNOWN_HEADER_ATTRS)
                        header_data = model_attrs
                        header_custom_attrs = custom_attrs

                    elif elem.tag == "tu":
                        current_tu_props_parsed, current_tu_notes_parsed, _, tu_raw_attrs = self._parse_common_elements(elem)
                        current_tu_props = current_tu_props_parsed
                        current_tu_notes = current_tu_notes_parsed
                        
                        model_attrs, custom_attrs = self._populate_model_attrs(tu_raw_attrs, KNOWN_TU_ATTRS)
                        current_tu_data = model_attrs
                        current_tu_custom_attrs_list = custom_attrs
                        current_tu_variants = []

                    elif elem.tag == "tuv" and current_tu_data is not None:
                        current_tuv_props_parsed, current_tuv_notes_parsed, _, tuv_raw_attrs = self._parse_common_elements(elem)
                        current_tuv_props = current_tuv_props_parsed
                        current_tuv_notes = current_tuv_notes_parsed

                        model_attrs, custom_attrs = self._populate_model_attrs(tuv_raw_attrs, KNOWN_TUV_ATTRS)
                        current_tuv_data = model_attrs
                        current_tuv_custom_attrs_list = custom_attrs
                        
                        # xml:lang is crucial and handled by populate_model_attrs if present
                        if 'lang' not in current_tuv_data and 'xml:lang' not in tuv_raw_attrs :
                            # Try to get it from parent TU or header if not present
                            # This is a fallback, ideally xml:lang is on TUV
                            parent_lang = current_tu_data.get('srclang') if current_tu_data else None
                            if not parent_lang and header_data:
                                parent_lang = header_data.get('srclang')
                            if parent_lang:
                                current_tuv_data['lang'] = parent_lang
                            else: # Default or raise error
                                print(f"Warning: xml:lang missing for TUV in TU {current_tu_data.get('tuid', 'N/A')}. Defaulting to 'und' (undefined).")
                                current_tuv_data['lang'] = 'und'


                    elif elem.tag == "seg" and current_tuv_data is not None:
                        # Capture the raw XML content of the <seg> element
                        # Preserve inner tags, comments, PIs, etc.
                        segment_content = "".join(etree.tostring(child, encoding='unicode', with_tail=True) for child in elem)
                        if elem.text and not segment_content: # Handle simple text content within seg
                            segment_content = elem.text 
                        
                        # The segment_xml should be the <seg>...</seg> itself
                        # We reconstruct the <seg> tag around its content.
                        # If seg has attributes, they should be part of the string.
                        # For now, assuming simple <seg>text</seg> or <seg><child>text</child></seg>
                        # A more robust way is to serialize `elem` itself.
                        current_tuv_data["segment_xml"] = etree.tostring(elem, encoding='unicode')


                elif event == "end":
                    if elem.tag == "header":
                        # Header parsing is mostly done at "start" due to its singular nature
                        pass
                    elif elem.tag == "tuv" and current_tuv_data is not None and current_tu_data is not None:
                        current_tuv_data["properties"] = current_tuv_props
                        current_tuv_data["notes"] = current_tuv_notes
                        current_tuv_data["custom_attributes"] = current_tuv_custom_attrs_list
                        
                        # Handle alias for xml:lang
                        if 'xml:lang' in current_tuv_data:
                             current_tuv_data['lang'] = current_tuv_data.pop('xml:lang')

                        try:
                            tuv_model = TranslationUnitVariant(**current_tuv_data)
                            current_tu_variants.append(tuv_model)
                        except Exception as e:
                            print(f"Error creating TUV model for TU {current_tu_data.get('tuid', 'N/A')}: {e}, data: {current_tuv_data}")

                        current_tuv_data = None # Reset for next TUV
                        current_tuv_props = []
                        current_tuv_notes = []
                        current_tuv_custom_attrs_list = []

                    elif elem.tag == "tu" and current_tu_data is not None:
                        current_tu_data["variants"] = current_tu_variants
                        current_tu_data["properties"] = current_tu_props
                        current_tu_data["notes"] = current_tu_notes
                        current_tu_data["custom_attributes"] = current_tu_custom_attrs_list
                        try:
                            tu_model = TranslationUnit(**current_tu_data)
                            translation_units.append(tu_model)
                        except Exception as e:
                            print(f"Error creating TU model: {e}, data: {current_tu_data}")
                        
                        current_tu_data = None # Reset for next TU
                        current_tu_variants = []
                        current_tu_props = []
                        current_tu_notes = []
                        current_tu_custom_attrs_list = []
                    
                    # Clear the element from memory to save space with iterparse
                    if elem.getparent() is not None: # Check if it's not the root
                         elem.clear()
            
            # After loop, clear root if it was parsed this way
            if root is not None:
                while root.getprevious() is not None:
                    del root.getparent()[0]

            if not header_data:
                raise ValueError("TMX file missing <header> element.")
            
            header_data["properties"] = header_properties
            header_data["notes"] = header_notes
            header_data["custom_attributes"] = header_custom_attrs
            
            # Handle alias for o-tmf, adminlang, srclang, o-encoding in header
            if 'o_tmf' in header_data : header_data['o-tmf'] = header_data.pop('o_tmf')
            if 'adminlang' in header_data : header_data['adminlang'] = header_data['adminlang'] # already correct
            if 'srclang' in header_data : header_data['srclang'] = header_data['srclang'] # already correct
            if 'o_encoding' in header_data : header_data['o-encoding'] = header_data.pop('o_encoding')


            tmx_header = TMXHeader(**header_data)
            return tmx_header, translation_units

        except etree.XMLSyntaxError as e:
            # Log error more informatively
            raise ValueError(f"Invalid XML syntax in TMX file: {filepath} - {e}") from e
        except Exception as e:
            # Catch other potential errors during parsing
            raise RuntimeError(f"An unexpected error occurred while parsing TMX file: {filepath} - {e}") from e

    def write_tmx_file(self, filepath: str, header: TMXHeader, translation_units: Iterable[TranslationUnit], indentation: int = 2):
        """
        Writes TMXHeader and TranslationUnit models to a TMX file.

        Args:
            filepath: Path where the TMX file will be saved.
            header: The TMXHeader model.
            translation_units: An iterable of TranslationUnit models.
            indentation: Number of spaces for pretty printing the XML.
        """
        # Note: `print` statements within this method are for basic diagnostics during development.
        # In a production environment, these should be replaced with a proper logging mechanism.

        # Namespace map for xml:lang
        NSMAP = {
            'xml': 'http://www.w3.org/XML/1998/namespace'
        }
        
        root = etree.Element("tmx", version="1.4")
        
        # Header
        header_attrs = header.dict(by_alias=True, exclude_none=True, exclude={'properties', 'notes', 'custom_attributes'})
        # Convert datetimes to TMX string format
        for dt_attr in ['creationdate', 'changedate']:
            if dt_attr in header_attrs and isinstance(header_attrs[dt_attr], datetime):
                header_attrs[dt_attr] = _datetime_to_tmx_str(header_attrs[dt_attr])

        header_element = etree.SubElement(root, "header", **header_attrs)

        for prop_model in header.properties:
            prop_attrs = {"type": prop_model.type}
            if prop_model.lang:
                prop_attrs[etree.QName(NSMAP['xml'], 'lang')] = prop_model.lang
            prop_elem = etree.SubElement(header_element, "prop", **prop_attrs)
            prop_elem.text = prop_model.value
        
        for note_model in header.notes:
            note_attrs = {}
            if note_model.lang:
                note_attrs[etree.QName(NSMAP['xml'], 'lang')] = note_model.lang
            if note_model.o_encoding:
                note_attrs["o-encoding"] = note_model.o_encoding
            note_elem = etree.SubElement(header_element, "note", **note_attrs)
            note_elem.text = note_model.text

        for custom_attr_model in header.custom_attributes:
            header_element.set(custom_attr_model.name, custom_attr_model.value)

        # Body
        body_element = etree.SubElement(root, "body")

        for tu_model in translation_units:
            tu_attrs = tu_model.dict(by_alias=True, exclude_none=True, exclude={'properties', 'notes', 'variants', 'custom_attributes'})
            for dt_attr in ['creationdate', 'changedate', 'lastusagedate']:
                 if dt_attr in tu_attrs and isinstance(tu_attrs[dt_attr], datetime):
                    tu_attrs[dt_attr] = _datetime_to_tmx_str(tu_attrs[dt_attr])
            
            tu_element = etree.SubElement(body_element, "tu", **tu_attrs)

            for prop_model in tu_model.properties:
                prop_attrs = {"type": prop_model.type}
                if prop_model.lang:
                    prop_attrs[etree.QName(NSMAP['xml'], 'lang')] = prop_model.lang
                prop_elem = etree.SubElement(tu_element, "prop", **prop_attrs)
                prop_elem.text = prop_model.value

            for note_model in tu_model.notes:
                note_attrs = {}
                if note_model.lang:
                    note_attrs[etree.QName(NSMAP['xml'], 'lang')] = note_model.lang
                if note_model.o_encoding:
                    note_attrs["o-encoding"] = note_model.o_encoding
                note_elem = etree.SubElement(tu_element, "note", **note_attrs)
                note_elem.text = note_model.text
            
            for custom_attr_model in tu_model.custom_attributes:
                tu_element.set(custom_attr_model.name, custom_attr_model.value)

            for tuv_model in tu_model.variants:
                tuv_attrs_dict = tuv_model.dict(by_alias=True, exclude_none=True, exclude={'properties', 'notes', 'segment_xml', 'custom_attributes', 'lang'})
                
                # Handle xml:lang separately due to namespace
                tuv_attrs_dict[etree.QName(NSMAP['xml'], 'lang')] = tuv_model.lang

                for dt_attr in ['creationdate', 'changedate', 'lastusagedate']:
                    if dt_attr in tuv_attrs_dict and isinstance(tuv_attrs_dict[dt_attr], datetime):
                        tuv_attrs_dict[dt_attr] = _datetime_to_tmx_str(tuv_attrs_dict[dt_attr])

                tuv_element = etree.SubElement(tu_element, "tuv", **tuv_attrs_dict)
                
                for prop_model in tuv_model.properties:
                    prop_attrs = {"type": prop_model.type}
                    if prop_model.lang:
                        prop_attrs[etree.QName(NSMAP['xml'], 'lang')] = prop_model.lang
                    prop_elem = etree.SubElement(tuv_element, "prop", **prop_attrs)
                    prop_elem.text = prop_model.value

                for note_model in tuv_model.notes:
                    note_attrs = {}
                    if note_model.lang:
                        note_attrs[etree.QName(NSMAP['xml'], 'lang')] = note_model.lang
                    if note_model.o_encoding:
                        note_attrs["o-encoding"] = note_model.o_encoding
                    note_elem = etree.SubElement(tuv_element, "note", **note_attrs)
                    note_elem.text = note_model.text

                for custom_attr_model in tuv_model.custom_attributes:
                    tuv_element.set(custom_attr_model.name, custom_attr_model.value)
                
                # Parse and append the segment_xml
                try:
                    # The segment_xml from the model should be a complete <seg>...</seg> string
                    if tuv_model.segment_xml.strip(): # Ensure it's not empty
                        seg_element_parsed = etree.fromstring(tuv_model.segment_xml)
                        tuv_element.append(seg_element_parsed)
                    else: # if segment_xml is empty, create an empty <seg/>
                        etree.SubElement(tuv_element, "seg")
                except etree.XMLSyntaxError as e:
                    print(f"Warning: Could not parse segment_xml for TUV (lang: {tuv_model.lang}, TU: {tu_model.tuid}): {e}. XML: '{tuv_model.segment_xml}'. Appending as empty <seg/>.")
                    etree.SubElement(tuv_element, "seg") # Append an empty seg to maintain structure


        tree = etree.ElementTree(root)
        tree.write(filepath, encoding='utf-8', xml_declaration=True, pretty_print=True)

# Example Usage (for testing purposes, normally not here)
if __name__ == '__main__':
    # Create dummy TMX data
    header_obj = TMXHeader(
        creationtool="MyTool",
        creationtoolversion="1.0",
        segtype="paragraph",
        adminlang="en",
        srclang="en-US",
        datatype="plaintext",
        o_tmf="TMX 1.4",
        creationdate=_parse_datetime_tmx("20231026T100000Z"),
        properties=[TMXProperty(type="Domain", value="Technical")],
        notes=[TMXNote(text="This is a test TMX file.")]
    )

    tu_list = [
        TranslationUnit(
            tuid="tu1",
            creationdate=_parse_datetime_tmx("20231026T100100Z"),
            properties=[TMXProperty(type="Client", value="ExampleCorp")],
            variants=[
                TranslationUnitVariant(
                    lang="en-US",
                    segment_xml="<seg>Hello World</seg>",
                    creationdate=_parse_datetime_tmx("20231026T100200Z"),
                    notes=[TMXNote(text="English source")]
                ),
                TranslationUnitVariant(
                    lang="fr-FR",
                    segment_xml="<seg>Bonjour le Monde</seg>",
                    creationdate=_parse_datetime_tmx("20231026T100300Z"),
                    properties=[TMXProperty(type="Status", value="Reviewed")]
                )
            ]
        ),
        TranslationUnit(
            tuid="tu2",
            datatype="html",
            variants=[
                TranslationUnitVariant(
                    lang="en-US",
                    segment_xml="<seg>This is <b>bold</b> text.</seg>"
                ),
                TranslationUnitVariant(
                    lang="es-ES",
                    segment_xml="<seg>Este es texto en <b>negrita</b>.</seg>"
                )
            ]
        )
    ]

    parser = TMXParser()
    test_file_path = "test_output.tmx"
    parser.write_tmx_file(test_file_path, header_obj, tu_list)
    print(f"TMX file written to {test_file_path}")

    # Test parsing
    try:
        parsed_header, parsed_tus = parser.parse_tmx_file(test_file_path)
        print("\n--- Parsed Header ---")
        print(parsed_header.json(indent=2, by_alias=True))
        print("\n--- Parsed TUs ---")
        for tu in parsed_tus:
            print(tu.json(indent=2, by_alias=True))
        
        # Verify some data
        assert parsed_header.creationtool == "MyTool"
        assert len(parsed_tus) == 2
        assert parsed_tus[0].tuid == "tu1"
        assert parsed_tus[0].variants[0].lang == "en-US"
        assert parsed_tus[0].variants[0].segment_xml == "<seg>Hello World</seg>"
        assert parsed_tus[1].variants[1].segment_xml == "<seg>Este es texto en <b>negrita</b>.</seg>"
        print("\nBasic parsing assertions passed.")

    except Exception as e:
        print(f"Error during parsing test: {e}")

    # Example of parsing a file that might have issues or specific structures
    # Create a more complex/problematic TMX for testing parser robustness
    bad_tmx_content = """<?xml version="1.0" encoding="UTF-8"?>
<tmx version="1.4">
  <header creationtool="TestTool" creationtoolversion="1.0" segtype="sentence" o-tmf="unknown" adminlang="en" srclang="en" datatype="plaintext" creationdate="20231115T120000Z">
    <note>Header note</note>
    <prop type="x-custom-header">Custom Header Prop</prop>
  </header>
  <body>
    <tu tuid="001" creationdate="20231115T120100Z" srclang="en" segtype="block" o-some-custom-tu-attr="val1">
      <prop type="x-texttype">title</prop>
      <prop type="x-ID">title-123</prop>
      <tuv xml:lang="en" creationdate="20231115T120200Z" o-some-custom-tuv-attr="val2">
        <prop type="x-variant-prop">English variant prop</prop>
        <seg>This is a <bpt i="1" type="bold">&lt;b&gt;</bpt>sample<ept i="1">&lt;/b&gt;</ept> segment.</seg>
      </tuv>
      <tuv xml:lang="de" changeid="user2" changedate="20231116T093000Z">
        <note>German translation, needs review.</note>
        <seg>Dies ist ein <bpt i="1" type="bold">&lt;b&gt;</bpt>Beispielsegment<ept i="1">&lt;/b&gt;</ept>.</seg>
      </tuv>
    </tu>
    <tu tuid="002">
        <tuv xml:lang="fr"><seg>Test simple.</seg></tuv>
    </tu>
  </body>
</tmx>"""
    test_complex_path = "test_complex.tmx"
    with open(test_complex_path, "w", encoding="utf-8") as f:
        f.write(bad_tmx_content)
    
    print(f"\n--- Parsing complex test file: {test_complex_path} ---")
    try:
        parsed_header_complex, parsed_tus_complex = parser.parse_tmx_file(test_complex_path)
        print("\n--- Parsed Complex Header ---")
        print(parsed_header_complex.json(indent=2, by_alias=True))
        print("\n--- Parsed Complex TUs ---")
        for tu in parsed_tus_complex:
            print(tu.json(indent=2, by_alias=True))
        
        assert parsed_header_complex.custom_attributes == [] # x-custom-header is not listed in KNOWN_HEADER_ATTRS
        assert len(parsed_tus_complex[0].custom_attributes) == 1
        assert parsed_tus_complex[0].custom_attributes[0].name == "o-some-custom-tu-attr"
        assert len(parsed_tus_complex[0].variants[0].custom_attributes) == 1
        assert parsed_tus_complex[0].variants[0].custom_attributes[0].name == "o-some-custom-tuv-attr"
        assert "This is a <bpt i=\"1\" type=\"bold\">&lt;b&gt;</bpt>sample<ept i=\"1\">&lt;/b&gt;</ept> segment." in parsed_tus_complex[0].variants[0].segment_xml
        print("\nComplex parsing assertions passed.")

    except Exception as e:
        print(f"Error during complex parsing test: {e}")
