from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator

# Helper for datetime fields
def current_time_utc() -> datetime:
    """Returns the current UTC datetime. For Pydantic field default_factory."""
    return datetime.utcnow()

class TMXAttribute(BaseModel):
    """Represents a generic key-value attribute, often used for custom data."""
    name: str = Field(description="Name of the attribute.")
    value: str = Field(description="Value of the attribute.")

class TMXProperty(BaseModel):
    """Represents a TMX <prop> element."""
    type: str = Field(description="Type of the property.")
    value: str = Field(description="Value of the property.")
    lang: Optional[str] = Field(None, description="Language of the property value, if applicable using xml:lang.")

class TMXNote(BaseModel):
    """Represents a TMX <note> element."""
    text: str = Field(description="Content of the note.")
    lang: Optional[str] = Field(None, description="Language of the note, if applicable using xml:lang.")
    o_encoding: Optional[str] = Field(None, description="Original encoding of the note.")

class TranslationUnitVariant(BaseModel):
    """Represents a TMX <tuv> element, containing translated content for a specific language."""
    segment_xml: str = Field(description="Raw XML content of the <seg> element, including inline tags.")
    segment_text_pure: Optional[str] = Field(None, description="Plain text content of the segment, with tags stripped, for searching/filtering.")
    lang: str = Field(..., alias="xml:lang", description="Language code for this variant (e.g., 'en-US'), corresponding to xml:lang attribute.")
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
    def _parse_datetime_str(cls, value: Any) -> Optional[datetime]:
        """Attempts to parse TMX-specific datetime strings into datetime objects."""
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y%m%dT%H%M%SZ')
            except ValueError:
                # Add more formats if necessary or raise a more specific error
                # Consider supporting just YYYYMMDD as well, if common in input TMX files
                raise ValueError(f"Invalid TMX datetime format: {value}. Expected YYYYMMDDThhmmssZ.")
        return value

class TranslationUnit(BaseModel):
    """Represents a TMX <tu> (Translation Unit) element."""
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
    def _parse_datetime_str(cls, value: Any) -> Optional[datetime]:
        """Attempts to parse TMX-specific datetime strings into datetime objects."""
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y%m%dT%H%M%SZ')
            except ValueError:
                raise ValueError(f"Invalid TMX datetime format: {value}. Expected YYYYMMDDThhmmssZ.")
        return value

class TMXHeader(BaseModel):
    """Represents the TMX <header> element, containing metadata about the TMX file."""
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
    def _parse_datetime_str(cls, value: Any) -> Optional[datetime]:
        """Attempts to parse TMX-specific datetime strings into datetime objects."""
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y%m%dT%H%M%SZ')
            except ValueError:
                raise ValueError(f"Invalid TMX datetime format: {value}. Expected YYYYMMDDThhmmssZ.")
        return value

class TMXFileContent(BaseModel):
    """Represents the entire content of a TMX file, including header and translation units."""
    header: TMXHeader
    translation_units: List[TranslationUnit]

class StatusResponse(BaseModel):
    """Generic response model for operations that return a status and an optional message."""
    status: str = Field(description="General status message (e.g., 'success', 'error', 'info').")
    message: Optional[str] = Field(None, description="Detailed message about the operation.")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details or error information.")

class FileInfoResponse(BaseModel):
    """Response model for providing information about the currently active TMX file session."""
    is_active: bool = Field(description="Whether a TMX file session is currently active.")
    file_path: Optional[str] = Field(None, description="Path of the currently active TMX file.")
    tmx_file_id: Optional[int] = Field(None, description="Database ID of the active TMX file entry in the session store.")
    header: Optional[TMXHeader] = Field(None, description="Parsed TMX header of the active file.")
    tu_count: Optional[int] = Field(None, description="Total number of translation units in the active file.")
    language_codes: Optional[List[str]] = Field(None, description="List of unique language codes in the active file.")
    # Other useful stats can be added later, e.g., average TUVs per TU, counts of properties/notes.

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None, # For TMXHeader datetimes if included
        }
        # Ensure that TMXHeader itself uses its own Config for datetime serialization if nested.

# --- API Request Models ---

class LanguageCodeRequest(BaseModel):
    """Request model for operations involving a single language code."""
    lang_code: str = Field(..., description="Language code, e.g., 'en-US', 'fr-FR'.")

class ChangeLanguageCodeRequest(BaseModel):
    """Request model for changing a language code."""
    old_lang_code: str = Field(..., description="The current language code to be changed.")
    new_lang_code: str = Field(..., description="The new language code to replace the old one.")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes.")

class SourceLanguageRequest(BaseModel):
    """Request model for operations that require specifying a source language and an optional user ID."""
    source_lang_code: str = Field(..., description="Source language code, e.g., 'en-US', 'fr-FR'.")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes or operations.")

class UserIdRequest(BaseModel):
    """Request model for operations that take an optional user ID."""
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking the operation.")

class SearchReplaceRequest(BaseModel):
    """Request model for search and replace operations."""
    search_text: str = Field(..., description="Text or regex pattern to search for.")
    replace_text: str = Field(description="Text to replace the found occurrences with.") # Allow empty for deletion
    lang_code: Optional[str] = Field(None, description="Optional language code to restrict the operation. If None, applies to all languages.")
    is_regex: bool = Field(False, description="Whether the search_text is a regular expression.")
    case_sensitive: bool = Field(False, description="Whether the search should be case sensitive.")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes.")

class MetadataItem(BaseModel): 
    """Generic model for representing a single metadata item (attribute, property, or note) in requests."""
    # This allows flexibility in request payloads.
    # Actual parsing to specific TMXAttribute, TMXProperty, TMXNote types happens in service/store if needed.
    name: Optional[str] = Field(None, description="Name of the item (used for TMXAttribute).")
    type: Optional[str] = Field(None, description="Type of the item (used for TMXProperty).")
    value: Optional[str] = Field(None, description="Value of the item (used for TMXAttribute, TMXProperty).")
    text: Optional[str] = Field(None, description="Text content of the item (used for TMXNote).")
    lang: Optional[str] = Field(None, description="Language code of the item (used for TMXProperty, TMXNote).")
    o_encoding: Optional[str] = Field(None, description="Original encoding (used for TMXNote).")
    # Allow any other fields if clients send more, though they might not be used by standard models
    class Config:
        extra = "allow"

# --- Data Conversion Request Models ---

class CSVImportOptions(BaseModel):
    """Options for importing data from a CSV file."""
    languages: List[str] = Field(..., description="List of language codes for columns, in order of appearance after TUID column (if any).")
    charset: str = Field('utf-8', description="Character set of the CSV file.")
    delimiter: str = Field(',', description="Delimiter used in the CSV file.")
    quotechar: str = Field('"', description="Quote character used in the CSV file.")
    tuid_col_index: Optional[int] = Field(None, description="0-based index of the column containing TUIDs. If None, TUIDs are auto-generated.")

class ExcelImportOptions(BaseModel):
    """Options for importing data from an Excel file."""
    languages: List[str] = Field(..., description="List of language codes, mapping to columns after TUID column (if any) or based on header_row_index.")
    sheet_name: Optional[str] = Field(None, description="Name of the Excel sheet to import. If None, the first active sheet is used.")
    header_row_index: Optional[int] = Field(1, description="1-based index of the row containing language headers. If None or invalid, 'languages' list order is critical.")
    data_start_row_index: int = Field(2, description="1-based index from where the actual data rows start.")
    tuid_col_letter: Optional[str] = Field(None, description="Column letter (e.g., 'A', 'B') for TUIDs. If None, TUIDs are auto-generated.")

class ExportPathRequest(BaseModel):
    """Request model for specifying the file path for an export operation."""
    file_path: str = Field(..., description="Server-local file path to save the exported file.")

class CSVExportOptions(BaseModel): 
    """Options for exporting data to a CSV file."""
    charset: str = Field('utf-8', description="Character set for the exported CSV file.")
    delimiter: str = Field(',', description="Delimiter for the exported CSV file.")


class MetadataUpdateRequest(BaseModel):
    """Request model for updating metadata (attributes, properties, notes) of a TU or TUV."""
    items: List[MetadataItem] = Field(..., description="List of metadata items (attributes, properties, or notes).")
    user_id: Optional[str] = Field(None, description="Optional user ID for tracking changes.")


class TUVariantDefinition(BaseModel):
    """Defines a translation unit variant for creating a new Translation Unit."""
    lang: str = Field(..., description="Language code of the variant.")
    segment_xml: str = Field(..., description="Raw XML content of the <seg> element for the variant.")
    # Allow other TUV fields optionally
    segment_text_pure: Optional[str] = Field(None, description="Plain text of the segment. Auto-populated if not provided.")
    creation_date: Optional[datetime] = Field(None, description="Creation date of the variant.")
    creation_id: Optional[str] = Field(None, description="ID of the creator of the variant.")
    change_date: Optional[datetime] = Field(None, description="Last modification date of the variant.")
    change_id: Optional[str] = Field(None, description="ID of the last modifier of the variant.")
    properties: Optional[List[MetadataItem]] = Field(None, description="Properties associated with this variant.")
    notes: Optional[List[MetadataItem]] = Field(None, description="Notes associated with this variant.")
    custom_attributes: Optional[List[MetadataItem]] = Field(None, description="Custom attributes for this variant.")
    usage_count: Optional[int] = Field(None, description="Usage count for this variant.")
    last_usage_date: Optional[datetime] = Field(None, description="Last usage date for this variant.")

    class Config:
        extra = "allow"


class TUDefinitionRequest(BaseModel):
    """Request model for creating a new Translation Unit."""
    tuid: Optional[str] = Field(None, description="Optional Translation Unit ID.")
    variants: List[TUVariantDefinition] = Field(..., description="List of translation unit variants (TUVs) for the new TU.")
    properties: Optional[List[MetadataItem]] = Field(None, description="Properties to associate with the new TU.")
    notes: Optional[List[MetadataItem]] = Field(None, description="Notes to associate with the new TU.")
    custom_attributes: Optional[List[MetadataItem]] = Field(None, description="Custom attributes for the new TU.")
    seg_type: Optional[str] = Field(None, description="Segment type for the TU (e.g., 'block', 'sentence').")
    datatype: Optional[str] = Field(None, description="Datatype of the TU content (e.g., 'plaintext', 'html').")
    usage_count: Optional[int] = Field(None, description="Usage count for the TU.")
    user_id: Optional[str] = Field(None, description="Optional user ID for setting creation_id/change_id fields.")

    class Config:
        extra = "allow"
