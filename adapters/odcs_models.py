"""
Pydantic models for ODCS (Open Data Contract Standard) structure.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ODCSConnection(BaseModel):
    """Database connection configuration."""
    
    type: str = Field(..., description="Connection type (postgres, snowflake, bigquery, etc.)")
    host: Optional[str] = Field(None, description="Database host")
    port: Optional[int] = Field(None, description="Database port")
    database: Optional[str] = Field(None, description="Database name")
    schema: Optional[str] = Field(None, description="Schema name")
    username: Optional[str] = Field(None, description="Username")
    password: Optional[str] = Field(None, description="Password")
    account: Optional[str] = Field(None, description="Snowflake account")
    warehouse: Optional[str] = Field(None, description="Snowflake warehouse")
    role: Optional[str] = Field(None, description="Snowflake role")
    extra_params: Optional[Dict[str, Any]] = Field(None, description="Additional connection parameters")


class ODCSColumn(BaseModel):
    """Column definition in a schema."""
    
    name: str = Field(..., description="Column name")
    type: str = Field(..., description="Column data type")
    nullable: bool = Field(True, description="Whether column can be null")
    primary_key: bool = Field(False, description="Whether column is primary key")
    unique: bool = Field(False, description="Whether column values must be unique")
    default: Optional[Any] = Field(None, description="Default value")
    description: Optional[str] = Field(None, description="Column description")
    constraints: Optional[Dict[str, Any]] = Field(None, description="Additional constraints")


class ODCSSchema(BaseModel):
    """Schema definition for a dataset."""
    
    name: str = Field(..., description="Schema/dataset name")
    columns: List[ODCSColumn] = Field(..., description="List of columns")
    primary_key: Optional[List[str]] = Field(None, description="Primary key columns")
    indexes: Optional[List[Dict[str, Any]]] = Field(None, description="Index definitions")
    description: Optional[str] = Field(None, description="Schema description")


class ODCSValidationRule(BaseModel):
    """Data quality validation rule."""
    
    type: str = Field(..., description="Rule type (not_null, format, unique, range, etc.)")
    column: Optional[str] = Field(None, description="Column name (if column-specific)")
    columns: Optional[List[str]] = Field(None, description="Column names (if multi-column)")
    pattern: Optional[str] = Field(None, description="Regex pattern for format validation")
    min_value: Optional[Any] = Field(None, description="Minimum value for range validation")
    max_value: Optional[Any] = Field(None, description="Maximum value for range validation")
    enum_values: Optional[List[Any]] = Field(None, description="Allowed values for enum validation")
    reference_table: Optional[str] = Field(None, description="Referenced table for referential integrity")
    reference_column: Optional[str] = Field(None, description="Referenced column for referential integrity")
    description: Optional[str] = Field(None, description="Rule description")


class ODCSQualityThresholds(BaseModel):
    """Quality metric thresholds."""
    
    drift_low: float = Field(5.0, description="Low drift threshold (percentage)")
    drift_medium: float = Field(15.0, description="Medium drift threshold (percentage)")
    drift_high: float = Field(30.0, description="High drift threshold (percentage)")
    freshness_hours: Optional[int] = Field(None, description="Maximum freshness in hours")
    availability: Optional[float] = Field(None, description="Minimum availability percentage")


class ODCSQualityConfig(BaseModel):
    """Quality configuration."""
    
    validation_rules: List[ODCSValidationRule] = Field(default_factory=list, description="Validation rules")
    thresholds: ODCSQualityThresholds = Field(default_factory=ODCSQualityThresholds, description="Quality thresholds")
    profiling_enabled: bool = Field(True, description="Enable profiling")
    profiling_schedule: Optional[str] = Field(None, description="Profiling schedule (cron)")
    drift_detection_enabled: bool = Field(True, description="Enable drift detection")
    drift_detection_strategy: str = Field("absolute_threshold", description="Drift detection strategy")


class ODCSMetadata(BaseModel):
    """Metadata for a dataset."""
    
    owner: Optional[str] = Field(None, description="Dataset owner")
    team: Optional[str] = Field(None, description="Owning team")
    tags: Optional[List[str]] = Field(None, description="Tags")
    description: Optional[str] = Field(None, description="Dataset description")
    lineage: Optional[List[Dict[str, Any]]] = Field(None, description="Data lineage")
    sla: Optional[Dict[str, Any]] = Field(None, description="Service level agreement")


class ODCSContract(BaseModel):
    """Complete ODCS contract combining schema, quality, and metadata."""
    
    version: str = Field("1.0", description="Contract version")
    name: str = Field(..., description="Contract/dataset name")
    schema: ODCSSchema = Field(..., description="Schema definition")
    quality: ODCSQualityConfig = Field(default_factory=ODCSQualityConfig, description="Quality configuration")
    metadata: ODCSMetadata = Field(default_factory=ODCSMetadata, description="Metadata")
    
    class Config:
        extra = "allow"


class ODCSDataset(BaseModel):
    """Dataset definition in ODCS config."""
    
    name: str = Field(..., description="Dataset name")
    source: str = Field(..., description="Source connection name")
    schema: Optional[str] = Field(None, description="Schema name")
    table: str = Field(..., description="Table name")
    contract: str = Field(..., description="Path to composed contract file")
    owner: Optional[str] = Field(None, description="Dataset owner")


class ODCSConfig(BaseModel):
    """Root ODCS configuration."""
    
    environment: str = Field("development", description="Environment name")
    connections: Dict[str, ODCSConnection] = Field(..., description="Connection definitions")
    datasets: List[ODCSDataset] = Field(..., description="Dataset definitions")
    quality: Optional[ODCSQualityConfig] = Field(None, description="Global quality configuration")

