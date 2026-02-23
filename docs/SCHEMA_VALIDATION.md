# Schema Validation System

Automated validation of API schemas and database migrations in the CI pipeline.

## Overview

The schema validation system ensures code quality and prevents schema drift by validating:

1. **API Request/Response Models** — Pydantic schemas in `app/schemas.py`
2. **Database Migrations** — Migration scripts in `migrations/`
3. **Database Schema** — SQLAlchemy models match actual database (existing)

All validators run automatically in GitHub Actions on every PR and push to main.

## Validators

### 1. API Schema Validator (`bin/validate_api_schemas.py`)

Validates Pydantic models used for API requests and responses.

**What it checks:**
- All Pydantic models can generate valid JSON schemas
- Schemas have proper field definitions and types
- No overly permissive `Any` types (warning)
- CRUD model consistency (Create/Update/Response models match Base)
- Update models have all fields optional (best practice)
- Model documentation (suggestions)

**Usage:**
```bash
# Validate schemas
python bin/validate_api_schemas.py

# Exit with code 1 if validation fails (for CI)
python bin/validate_api_schemas.py --check

# Export schemas to JSON for documentation
python bin/validate_api_schemas.py --export api-schemas.json
```

**Example output:**
```
⚠️  Found 9 schema warning(s):

  [BudgetLimits] untyped_object
    Field 'per_provider_monthly_usd' uses Any/dict - consider defining explicit schema

  [TaskStatusUpdate] update_required_fields
    Update model has required fields: ['status']. Update models should typically have all optional fields.

💡 Found 116 schema suggestion(s):
  (Informational suggestions about missing docstrings)

Summary: 0 errors, 9 warnings, 116 suggestions
```

### 2. Migration Validator (`bin/validate_migrations.py`)

Validates database migration scripts for correctness and safety.

**What it checks:**
- Python syntax is valid
- Naming conventions (descriptive names with underscores)
- Contains proper migration functions (`main`, `migrate`, `up`, `upgrade`, `apply`)
- Dangerous operations flagged (DROP TABLE, TRUNCATE, UPDATE without WHERE)
- No hardcoded credentials or secrets
- No suspicious imports

**Usage:**
```bash
# Validate migrations
python bin/validate_migrations.py

# Exit with code 1 if validation fails (for CI)
python bin/validate_migrations.py --check

# Specify migrations directory
python bin/validate_migrations.py --dir migrations/
```

**Example output:**
```
⚠️  Found 23 migration warning(s):

  [create_topics_table.py:51] unsafe_operation
    UPDATE without WHERE clause detected: Unqualified UPDATE can modify all rows. 
    Ensure this is intentional (e.g., backfilling data).

  [create_learning_tables.py:87] dangerous_operation
    DROP TABLE detected: Dropping tables is irreversible

Summary: 0 errors, 23 warnings, 0 suggestions
```

### 3. Database Schema Validator (`bin/validate_schema.py`)

Validates SQLAlchemy models match the actual database schema (existing tool).

**What it checks:**
- All model tables exist in database
- All model columns exist with correct types
- No missing or extra columns
- Correct nullable constraints
- Foreign key constraints match

**Usage:**
```bash
# Validate database schema
python bin/validate_schema.py

# Exit with code 1 if validation fails (for CI)
python bin/validate_schema.py --check
```

## CI Integration

All validators run automatically in the GitHub Actions workflow (`.github/workflows/validation.yml`).

**Jobs:**
1. `schema-validation` — Validates SQLAlchemy models vs database
2. `api-schema-validation` — Validates Pydantic API schemas
3. `migration-validation` — Validates migration scripts
4. `test-time-detector` — Detects time-based test issues
5. `security-scan` — Runs Bandit and Safety checks

**Triggered on:**
- Pull requests to `main`
- Pushes to `main`
- Changes to relevant files:
  - `app/models.py`
  - `app/schemas.py`
  - `app/database.py`
  - `migrations/**`
  - `tests/**`
  - Validation scripts themselves

## Adding New Models

### Pydantic Models (API schemas)

When adding new API models to `app/schemas.py`:

1. **Create a Base model** with common fields:
   ```python
   class WidgetBase(BaseModel):
       title: str
       description: Optional[str] = None
       active: bool = True
   ```

2. **Create CRUD variants:**
   ```python
   class WidgetCreate(WidgetBase):
       id: str

   class WidgetUpdate(BaseModel):
       # All fields optional for updates
       title: Optional[str] = None
       description: Optional[str] = None
       active: Optional[bool] = None

   class Widget(WidgetBase):
       id: str
       created_at: datetime
       updated_at: datetime
       
       model_config = ConfigDict(from_attributes=True)
   ```

3. **Run validator:**
   ```bash
   python bin/validate_api_schemas.py
   ```

4. **Add docstrings** to address suggestions (optional but recommended).

### Database Migrations

When creating new migrations in `migrations/`:

1. **Use descriptive names:**
   ```
   create_widgets_table.py
   add_status_to_tasks.py
   ```

2. **Include required function:**
   ```python
   """Create widgets table for widget management."""
   
   async def migrate():
       # Migration code here
       pass
   ```

3. **Add docstring** explaining what the migration does.

4. **Run validator:**
   ```bash
   python bin/validate_migrations.py
   ```

5. **Review warnings** for dangerous operations — ensure they're intentional.

## Testing

All validators have comprehensive tests in `tests/test_schema_validators.py`.

**Run tests:**
```bash
python -m pytest tests/test_schema_validators.py -v
```

**Test coverage:**
- Model discovery and schema generation
- Validation logic (CRUD consistency, dangerous operations)
- CLI interfaces
- Integration tests (both validators pass on current codebase)

## Severity Levels

### Errors (🔴)
Block CI pipeline. Must be fixed before merge.
- Invalid Python syntax
- Missing migration functions
- Schema generation failures
- Missing base fields in response models
- Hardcoded credentials
- DELETE without WHERE clause

### Warnings (⚠️)
Don't block CI but require attention.
- Dangerous operations (DROP TABLE, TRUNCATE)
- UPDATE without WHERE clause (common in migrations)
- Overly permissive `Any` types
- Update models with required fields
- Suspicious imports

### Info/Suggestions (💡)
Optional improvements, informational only.
- Missing docstrings
- Short migration names

## Maintenance

### Updating Validators

1. **Modify validator script** (`bin/validate_api_schemas.py` or `bin/validate_migrations.py`)
2. **Update tests** in `tests/test_schema_validators.py`
3. **Run tests** to ensure changes work
4. **Update this documentation** if adding new checks

### Adjusting Strictness

If validators are too strict or too lenient:

1. **Edit severity levels** in the validator classes
2. **Add/remove checks** as needed
3. **Update tests** to match new expectations
4. **Document changes** in this file

## Best Practices

### API Schemas
- ✅ Use explicit types, avoid `Any`
- ✅ All Update models have optional fields
- ✅ Response models include all Base fields
- ✅ Add docstrings for complex models
- ✅ Use `ConfigDict(from_attributes=True)` for ORM models

### Migrations
- ✅ Descriptive names (`create_*`, `add_*`, `update_*`)
- ✅ Include migration docstring
- ✅ Use `migrate()` or `main()` function
- ✅ Add WHERE clauses to UPDATE/DELETE
- ✅ Review dangerous operations carefully
- ⚠️ Never commit hardcoded credentials
- ⚠️ Test migrations before committing

## Troubleshooting

### "No migration function found"
- Ensure your migration has `migrate()`, `main()`, `up()`, `upgrade()`, or `apply()` function
- Check function is defined at module level (not nested)

### "UPDATE without WHERE clause"
- This is a warning, not an error (won't block CI)
- If backfilling data, this is expected — ensure it's intentional
- Add WHERE clause if you're updating specific rows

### "Overly permissive Any types"
- Consider defining explicit schema for JSON/dict fields
- Use `dict[str, Any]` if you need flexibility
- Document why if `Any` is necessary

### "Missing base fields"
- Ensure Response model includes all fields from Base
- Check inheritance: `class Widget(WidgetBase)`
- Verify `model_config = ConfigDict(from_attributes=True)`

## Related Documentation

- [CONTRIBUTING.md](../CONTRIBUTING.md) — Development guidelines
- [ARCHITECTURE.md](../ARCHITECTURE.md) — System architecture
- [Testing Guide](TESTING.md) — Running and writing tests
- [GitHub Actions Workflow](../.github/workflows/validation.yml) — CI configuration

## Changelog

**2026-02-23** — Initial schema validation system
- Added API schema validator
- Added migration validator
- Integrated into CI pipeline
- Added comprehensive tests
