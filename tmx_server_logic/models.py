from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator

# Helper for datetime fields
def current_time_utc():
    return datetime.utcnow()

class TMXAttribute(BaseModel):
    name: str = Field(description="Name of the attribute.")
    value: str = Field(description="Value of the attribute.")

class TMXProperty(BaseModel):
    type: str = Field(description="Type of the property.")
    value: str = Field(description="Value of the property.")
    lang: Optional[str] = Field(None, description="Language of the property value, if applicable.")

class TMXNote(BaseModel):
    text: str = Field(description="Content of the note.")
    lang: Optional[str] = Field(None, description="Language of the note, if applicable.")
    o_encoding: Optional[str] = Field(None, description="Original encoding of the note.")

class TranslationUnitVariant(BaseModel):
    segment_xml: str = Field(description="Raw XML content of the <seg> element.")
    segment_text_pure: Optional[str] = Field(None, description="Plain text content of the segment, for searching/filtering")
    lang: str = Field(..., alias="xml:lang", description="Language code for this variant (e.g., 'en-US').")
    creation_date: Optional[datetime] = Field(default_factory=current_time_utc, alias="creationdate")
    creation_id: Optional[str] = Field(None, alias="creationid")
    change_date: Optional[datetime] = Field(default_factory=current_time_utc, alias="changedate")
    change_id: Optional[str] = Field(None, alias="changeid")
    last_usage_date: Optional[datetime] = Field(None, alias="lastusagedate")
    usage_count: Optional[int] = Field(None, alias="usagecount")
    
    properties: List[TMXProperty] = Field(default_factory=list, description="List of <prop> elements associated with the TUV.")
    notes: List[TMXNote] = Field(default_factory=list, description="List of <note> elements associated with the TUV.")
    
    # For custom attributes not defined in the standard TMX spec
    custom_attributes: List[TMXAttribute] = Field(default_factory=list)

    class Config:
        allow_population_by_field_name = True
        extra = 'allow' # Allow extra fields, which will be captured in custom_attributes if needed by parser
        json_encoders = {
            datetime: lambda v: v.strftime('%Y%m%dT%H%M%SZ') if v else None
        }

    @validator('creation_date', 'change_date', 'last_usage_date', pre=True)
    def _parse_datetime_str(cls, value):
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y%m%dT%H%M%SZ')
            except ValueError:
                # Add more formats if necessary or raise a more specific error
                raise ValueError(f"Invalid datetime format: {value}")
        return value

class TranslationUnit(BaseModel):
    tuid: Optional[str] = Field(None, description="Translation Unit ID.")
    usage_count: Optional[int] = Field(None, alias="usagecount", description="How many times the TU has been used.")
    last_usage_date: Optional[datetime] = Field(None, alias="lastusagedate", description="When the TU was last used.")
    creation_date: Optional[datetime] = Field(default_factory=current_time_utc, alias="creationdate")
    creation_id: Optional[str] = Field(None, alias="creationid")
    change_date: Optional[datetime] = Field(default_factory=current_time_utc, alias="changedate")
    change_id: Optional[str] = Field(None, alias="changeid")
    seg_type: Optional[str] = Field(None, alias="segtype", description="Segment type (e.g., 'block', 'paragraph', 'sentence', 'phrase').")
    datatype: Optional[str] = Field(None, description="Datatype of the content (e.g., 'xml', 'html', 'plaintext').")
    srclang: Optional[str] = Field(None, description="Source language for the TU, if different from header.")
    
    properties: List[TMXProperty] = Field(default_factory=list, description="List of <prop> elements associated with the TU.")
    notes: List[TMXNote] = Field(default_factory=list, description="List of <note> elements associated with the TU.")
    variants: List[TranslationUnitVariant] = Field(description="List of translation unit variants (TUVs).")
    
    # For custom attributes
    custom_attributes: List[TMXAttribute] = Field(default_factory=list)

    class Config:
        allow_population_by_field_name = True
        extra = 'allow'
        json_encoders = {
            datetime: lambda v: v.strftime('%Y%m%dT%H%M%SZ') if v else None
        }

    @validator('creation_date', 'change_date', 'last_usage_date', pre=True)
    def _parse_datetime_str(cls, value):
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y%m%dT%H%M%SZ')
            except ValueError:
                raise ValueError(f"Invalid datetime format: {value}")
        return value

class TMXHeader(BaseModel):
    creation_tool: Optional[str] = Field(None, alias="creationtool", description="Name of the tool that created the TMX file.")
    creation_tool_version: Optional[str] = Field(None, alias="creationtoolversion", description="Version of the creation tool.")
    seg_type: str = Field(..., alias="segtype", description="Default segment type for TUs (e.g., 'block', 'paragraph').")
    o_tmf: Optional[str] = Field(None, alias="o-tmf", description="Original TMX format (e.g., 'TMX TMX_1.4a').")
    admin_lang: str = Field(..., alias="adminlang", description="Administrative language (e.g., 'en-US').")
    src_lang: str = Field(..., alias="srclang", description="Source language of the TMX file.")
    datatype: str = Field(..., description="Datatype of the content (e.g., 'xml', 'html', 'plaintext').")
    o_encoding: Optional[str] = Field(None, alias="o-encoding", description="Original encoding of the file.")
    creation_date: Optional[datetime] = Field(default_factory=current_time_utc, alias="creationdate")
    creation_id: Optional[str] = Field(None, alias="creationid")
    change_date: Optional[datetime] = Field(default_factory=current_time_utc, alias="changedate")
    change_id: Optional[str] = Field(None, alias="changeid")

    properties: List[TMXProperty] = Field(default_factory=list, description="List of <prop> elements associated with the header.")
    notes: List[TMXNote] = Field(default_factory=list, description="List of <note> elements associated with the header.")
    
    # For custom attributes
    custom_attributes: List[TMXAttribute] = Field(default_factory=list)

    class Config:
        allow_population_by_field_name = True
        json_encoders = {
            datetime: lambda v: v.strftime('%Y%m%dT%H%M%SZ') if v else None
        }

    @validator('creation_date', 'change_date', pre=True)
    def _parse_datetime_str(cls, value):
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y%m%dT%H%M%SZ')
            except ValueError:
                raise ValueError(f"Invalid datetime format: {value}")
        return value

class TMXFileContent(BaseModel):
    header: TMXHeader
    translation_units: List[TranslationUnit]

class StatusResponse(BaseModel):
    status: str = Field(description="General status message (e.g., 'success', 'error').")
    message: Optional[str] = Field(None, description="Detailed message about the operation.")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details or error information.")

class FileInfoResponse(BaseModel):
    is_active: bool = Field(description="Whether a TMX file session is currently active.")
    file_path: Optional[str] = Field(None, description="Path of the currently active TMX file.")
    tmx_file_id: Optional[int] = Field(None, description="Database ID of the active TMX file entry.")
    header: Optional[TMXHeader] = Field(None, description="Parsed TMX header of the active file.")
    tu_count: Optional[int] = Field(None, description="Total number of translation units in the active file.")
    language_codes: Optional[List[str]] = Field(None, description="List of unique language codes in the active file.")
    # Other useful stats can be added later, e.g., average TUVs per TU, counts of properties/notes.

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None, # For TMXHeader datetimes if included
        }
        # Ensure that TMXHeader itself uses its own Config for datetime serialization if nested.
        # Pydantic v2 handles this better by default. For v1, ensure TMXHeader's json_encoders are respected.

# --- Request Models for Chunk 5 ---

class LanguageCodeRequest(BaseModel):
    lang_code: str = Field(..., description="Language code, e.g., 'en-US', 'fr-FR'.")

class ChangeLanguageCodeRequest(BaseModel):
    old_lang_code: str = Field(..., description="The current language code to be changed.")
    new_lang_code: str = Field(..., description="The new language code to replace the old one.")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes.")

class SourceLanguageRequest(BaseModel):
    source_lang_code: str = Field(..., description="Source language code, e.g., 'en-US', 'fr-FR'.")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes or operations.")

# --- Request Models for Chunk 5 Part 3 ---

class UserIdRequest(BaseModel):
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking the operation.")

class SearchReplaceRequest(BaseModel):
    search_text: str = Field(..., description="Text or regex pattern to search for.")
    replace_text: str = Field(description="Text to replace the found occurrences with.") # Allow empty for deletion
    lang_code: Optional[str] = Field(None, description="Optional language code to restrict the operation. If None, applies to all languages.")
    is_regex: bool = Field(False, description="Whether the search_text is a regular expression.")
    case_sensitive: bool = Field(False, description="Whether the search should be case sensitive.")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes.")

class MetadataItem(BaseModel): # Generic for TMXAttribute, TMXProperty, TMXNote content
    # This allows flexibility, actual parsing to specific types happens in service/store if needed
    # For setting, the service will expect List[Dict] which can be directly passed to Pydantic models
    name: Optional[str] = None # For TMXAttribute
    type: Optional[str] = None # For TMXProperty
    value: Optional[str] = None # For TMXAttribute, TMXProperty
    text: Optional[str] = None  # For TMXNote
    lang: Optional[str] = None # For TMXProperty, TMXNote
    o_encoding: Optional[str] = None # For TMXNote
    # Allow any other fields if clients send more, though they might not be used by standard models
    class Config:
        extra = "allow"

# --- Request Models for Chunk 6 (Data Conversion) ---

class CSVImportRequest(BaseModel):
    languages: List[str] = Field(..., description="List of language codes for columns, in order of appearance after TUID column (if any).")
    charset: str = Field('utf-8', description="Character set of the CSV file.")
    delimiter: str = Field(',', description="Delimiter used in the CSV file.")
    quotechar: str = Field('"', description="Quote character used in the CSV file.")
    tuid_col_index: Optional[int] = Field(None, description="0-based index of the column containing TUIDs. If None, TUIDs are auto-generated.")

class ExcelImportRequest(BaseModel):
    languages: List[str] = Field(..., description="List of language codes, mapping to columns after TUID column (if any) or based on header_row_index.")
    sheet_name: Optional[str] = Field(None, description="Name of the Excel sheet to import. If None, the first active sheet is used.")
    header_row_index: Optional[int] = Field(1, description="1-based index of the row containing language headers. If None or invalid, 'languages' list order is critical.")
    data_start_row_index: int = Field(2, description="1-based index from where the actual data rows start.")
    tuid_col_letter: Optional[str] = Field(None, description="Column letter (e.g., 'A', 'B') for TUIDs. If None, TUIDs are auto-generated.")

class CSVExportRequest(BaseModel):
    charset: str = Field('utf-8', description="Character set for the exported CSV file.")
    delimiter: str = Field(',', description="Delimiter for the exported CSV file.")


class MetadataUpdateRequest(BaseModel):
    items: List[MetadataItem] = Field(..., description="List of metadata items (attributes, properties, or notes).")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes.")


# Minimal TUV data for TU creation
class TUVariantDefinition(BaseModel):
    lang: str
    segment_xml: str
    # Allow other TUV fields optionally
    segment_text_pure: Optional[str] = None # Will be auto-populated if not provided
    creation_date: Optional[datetime] = None
    creation_id: Optional[str] = None
    change_date: Optional[datetime] = None
    change_id: Optional[str] = None
    properties: Optional[List[MetadataItem]] = None # Allow passing as dicts
    notes: Optional[List[MetadataItem]] = None
    custom_attributes: Optional[List[MetadataItem]] = None
    usage_count: Optional[int] = None
    last_usage_date: Optional[datetime] = None

    class Config:
        extra = "allow"


class TUDefinitionRequest(BaseModel):
    tuid: Optional[str] = None
    variants: List[TUVariantDefinition]
    properties: Optional[List[MetadataItem]] = None
    notes: Optional[List[MetadataItem]] = None
    custom_attributes: Optional[List[MetadataItem]] = None
    seg_type: Optional[str] = None
    datatype: Optional[str] = None
    usage_count: Optional[int] = None
    # original_position: Optional[int] = None # Handled by store if new, or by specific reordering endpoint
    user_id: Optional[str] = Field(None, description="Optional user ID for creation_id/change_id.")

    class Config:
        extra = "allow"
