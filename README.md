# Hospital Management System – Backend

A robust, scalable REST API backend for comprehensive hospital management. Built with FastAPI, Python, and PostgreSQL for high performance and reliability.

This project is part of a **multi-tenant Hospital Management System (HMS)** designed to handle patient records, appointments, prescriptions, admissions, and administrative operations for hospitals and healthcare facilities.

This project was created as part of **HackArena 2.0 by Masaiverse x Platform Commons**, which was held on 29th & 30th November 2025!

## 🔗 Related Repositories

- **[Frontend Repository](https://github.com/vs-ai-ds/hms-frontend)** - React-based frontend application
- **Backend Repository** (this repository) - FastAPI-based backend API


## 🌐 Live Demo

[![Backend Live](https://img.shields.io/badge/Backend-API-blue)](https://hms-backend-5z1l.onrender.com/)

[![Frontend Live](https://img.shields.io/badge/Frontend-Live-green)](https://hms-backend-5z1l.onrender.com/redoc)


## ✨ Features

### 🏗️ Architecture

#### Multi-Tenant Architecture
- **Schema-Per-Tenant**: Each hospital operates in its own PostgreSQL schema
- **Complete Data Isolation**: Tenant data is completely isolated at the database level
- **Automatic Schema Management**: Tenant schemas created automatically on registration
- **Search Path Management**: Automatic tenant context switching using PostgreSQL `search_path`
- **Public Schema**: Shared tables (users, tenants, permissions) in public schema
- **Tenant Schema**: Isolated tables (patients, appointments, prescriptions) per tenant

#### Database Design
- **PostgreSQL 16+**: Modern PostgreSQL with advanced features
- **Alembic Migrations**: Version-controlled database schema changes
- **Foreign Key Constraints**: Referential integrity across schemas
- **Indexes**: Optimized indexes for performance
- **Enums**: Type-safe enums for status fields

### 🔐 Authentication & Authorization

#### Authentication
- **JWT Tokens**: Secure token-based authentication
- **OAuth2 Password Flow**: Standard OAuth2 password grant type
- **Token Refresh**: Automatic token refresh mechanism
- **Password Hashing**: bcrypt with passlib for secure password storage
- **Password History**: Track password history to prevent reuse
- **Email Verification**: Secure email verification flow
- **Password Reset**: Secure password reset with token-based flow
- **First Login**: Force password change on first login

#### Authorization
- **Role-Based Access Control (RBAC)**: Granular permissions per role
- **Permission-Based Endpoints**: Endpoints protected by permission codes
- **Attribute-Based Access Control (ABAC)**: Context-aware access control
- **Super Admin**: Platform-level administration across all tenants
- **Tenant Context**: Automatic tenant context in all requests

### 👥 Patient Management

#### Patient Operations
- **Create Patient**: Full patient registration with comprehensive data
- **Update Patient**: Update patient information
- **List Patients**: Advanced filtering and search
- **Get Patient**: Detailed patient information
- **Patient Search**: Search by name, phone, patient code, national ID
- **Patient Export**: Export patient data (CSV/Excel)

#### Patient Features
- **Duplicate Detection**: Smart detection of existing patients
- **Patient Code Generation**: Automatic unique patient code
- **Visit Tracking**: Automatic last visit tracking
- **Patient Type**: OPD (Outpatient) and IPD (Inpatient) support
- **Comprehensive Data**: 
  - Personal information
  - Contact details
  - Address information
  - Emergency contacts
  - Medical information (blood group, allergies, chronic conditions)
  - Consent management

### 📅 Appointments (OPD)

#### Appointment Management
- **Create Appointment**: Schedule appointments with doctors
- **List Appointments**: Advanced filtering by status, date, doctor, department
- **Update Appointment**: Modify appointment details
- **Appointment Actions**:
  - Check-in (receptionist)
  - Start consultation (doctor)
  - Complete appointment
  - Cancel appointment
  - Mark no-show
  - Reschedule appointment

#### Appointment Features
- **Status Workflow**: Enforced status transitions
- **15-Minute Intervals**: All appointments in 15-minute blocks
- **Eligibility Checking**: Prevent duplicate appointments
- **Time Validation**: Past appointment prevention
- **Lifecycle Tracking**: Automatic timestamp recording
- **Notification Support**: Email and SMS notifications

### 💊 Prescriptions

#### Prescription Management
- **Create Prescription**: Link to appointments or admissions
- **List Prescriptions**: Filter by patient, doctor, status, date
- **Update Prescription**: Modify prescription details
- **Prescription Status**: DRAFT → ISSUED → DISPENSED workflow
- **Prescription Items**: Multiple medicines per prescription
- **Stock Integration**: Automatic stock checking and deduction

#### Prescription Features
- **Medicine Catalog**: Select from stock items
- **Dosage Management**: Configure dosage, frequency, duration
- **Instructions**: Patient instructions per item
- **Prescription Codes**: Unique prescription identifiers
- **Print Support**: PDF generation for prescriptions
- **Cancellation**: Cancel prescriptions with reason

### 🏥 Admissions (IPD)

#### Admission Management
- **Create Admission**: Admit patients to IPD
- **List Admissions**: Filter by status, department, doctor
- **Update Admission**: Modify admission details
- **Discharge**: Complete discharge workflow with summary
- **Admission Status**: ACTIVE and DISCHARGED states

#### Admission Features
- **Department Assignment**: Assign to specific departments
- **Primary Doctor**: Assign primary doctor
- **Admission Duration**: Automatic length of stay calculation
- **Discharge Summary**: Clinical summary on discharge
- **Prescription Linking**: Link prescriptions to admissions
- **Vitals Tracking**: Record vitals for IPD patients

### 💉 Vitals Management

#### Vitals Operations
- **Record Vitals**: Comprehensive vital signs recording
- **Vitals History**: View historical vitals data
- **OPD Vitals**: Vitals for outpatient appointments
- **IPD Vitals**: Vitals for inpatient admissions

#### Vitals Data
- Blood pressure (systolic/diastolic)
- Heart rate
- Temperature
- Respiratory rate
- SpO2 (oxygen saturation)
- Weight and height
- BMI calculation

### 👤 User Management

#### User Operations
- **Create User**: Add staff members with roles
- **List Users**: Advanced filtering and search
- **Update User**: Modify user information
- **Deactivate User**: Soft delete users
- **Password Reset**: Admin-triggered password reset
- **User Roles**: Assign multiple roles per user

#### User Features
- **Role Assignment**: Multiple roles per user
- **Department Assignment**: Assign to departments
- **Permission Management**: Role-based permissions
- **User Status**: Active/inactive status
- **Email Verification**: Track verification status
- **Activity Tracking**: Track user activity

### 🏢 Department Management

#### Department Operations
- **Create Department**: Add new departments
- **List Departments**: View all departments
- **Update Department**: Modify department details
- **Delete Department**: Remove departments (with validation)

#### Department Features
- **Department Types**: Patient-facing and staff-only departments
- **Department Assignment**: Assign staff to departments
- **Default Departments**: System default departments
- **Department Validation**: Prevent deletion if in use

### 🔐 Roles & Permissions

#### Role Management
- **System Roles**: Pre-defined roles (HOSPITAL_ADMIN, DOCTOR, NURSE, PHARMACIST, RECEPTIONIST)
- **Custom Roles**: Create custom roles
- **Permission Assignment**: Assign permissions to roles
- **Role Permissions**: View and manage role permissions

#### Permission System
- **Permission Categories**: Organized by feature area
- **Granular Permissions**: Fine-grained access control
- **Permission Codes**: Standardized permission codes
- **Permission Validation**: Endpoint-level permission checking

### 📦 Stock Management

#### Stock Operations
- **Create Stock Item**: Add medicines, equipment, consumables
- **List Stock Items**: Filter by type, search by name
- **Update Stock Item**: Modify stock item details
- **Stock Tracking**: Automatic stock level updates
- **Low Stock Alerts**: Alerts for items below reorder level

#### Stock Features
- **Stock Types**: MEDICINE, EQUIPMENT, CONSUMABLE
- **Medicine Details**: Form, strength, route, dosage, frequency
- **Stock Levels**: Current stock and reorder level
- **Stock Deduction**: Automatic on prescription dispensing
- **Stock History**: Track stock changes

### 🤝 Patient Sharing

#### Sharing Operations
- **Create Sharing Request**: Share patients with other hospitals
- **List Sharing Requests**: View sent and received requests
- **Accept/Reject Sharing**: Manage sharing requests
- **Shared Patients**: View patients shared with your hospital
- **Read-Only Links**: Generate secure read-only access links

#### Sharing Features
- **Cross-Tenant Sharing**: Share between different hospitals
- **Sharing Modes**: Read-only and read-write modes
- **Token-Based Access**: Secure token-based sharing
- **Expiration**: Automatic expiration of sharing links
- **Access Control**: Permission-based sharing

### 🏢 Platform Management (Super Admin)

#### Tenant Operations
- **List Tenants**: View all hospital tenants
- **Tenant Details**: Detailed tenant information and metrics
- **Activate/Suspend Tenants**: Manage tenant status
- **Reset Admin Password**: Reset tenant admin passwords
- **Tenant Metrics**: Platform-wide statistics
- **User Limits**: Set maximum number of users per tenant
- **Patient Limits**: Set maximum number of patients per tenant


#### Platform Features
- **Multi-Tenant Administration**: Manage all tenants
- **Tenant Status**: PENDING, VERIFIED, ACTIVE, SUSPENDED, INACTIVE
- **Tenant Metrics**: Aggregated metrics across tenants
- **Demo Data Management**: Seed, freshen, and reset demo data

### 📊 Dashboard & Analytics

#### Dashboard Metrics
- **Role-Based Dashboards**: Different metrics per role
- **Real-Time Metrics**: Today's activity, pending actions
- **Status Breakdowns**: Appointments and prescriptions by status
- **Trend Analysis**: Patient registrations over time
- **Platform Metrics**: Super Admin platform-wide statistics

#### Analytics Features
- **Patient Registrations**: Daily/weekly/monthly trends
- **Appointment Statistics**: Status distribution
- **Prescription Statistics**: Status and trends
- **Gender Distribution**: Patient demographics
- **Age Distribution**: Patient age groups

### 📄 Documents Management

#### Document Operations
- **Upload Documents**: Upload patient documents
- **List Documents**: View patient documents
- **Download Documents**: Secure document download
- **Delete Documents**: Remove documents

### 🔔 Notifications

#### Notification Channels
- **Email Notifications**: 
  - Resend API integration
  - SMTP support
  - Email templates
  - Sandbox mode for testing
- **SMS Notifications**:
  - Twilio integration
  - SMS provider abstraction
  - Configurable SMS provider
- **WhatsApp Notifications**:
  - WhatsApp provider abstraction
  - Future integration support

#### Notification Features
- **Patient Notifications**: Email/SMS to patients
- **Staff Notifications**: Internal notifications
- **Template System**: Reusable email templates
- **Notification Logging**: Track all notifications sent

### 🧪 Demo Data Management

#### Demo Operations
- **Seed Demo Data**: Create comprehensive demo data
- **Freshen Demo Data**: Shift demo data dates forward
- **Reset Demo Data**: Clear all demo data
- **Auto-Refresh**: Automatic demo data refresh on login

#### Demo Features
- **Two Demo Tenants**: Pre-configured demo tenants (A and B)
- **Realistic Data**: 
  - 100 patients per tenant
  - 500 appointments per tenant
  - 200 IPD admissions per tenant
  - 220 prescriptions per tenant
  - Stock items and vitals
- **Weighted Distribution**: Realistic date distribution
- **Demo Credentials**: Pre-configured demo accounts

---

## 🛠️ Tech Stack

### Core Framework
- **FastAPI 0.115+** - Modern, fast web framework for building APIs
- **Python 3.11+** - Modern Python with type hints
- **Uvicorn** - ASGI server for FastAPI
- **Pydantic 2.7+** - Data validation using Python type annotations
- **Pydantic Settings** - Settings management

### Database & ORM
- **PostgreSQL 16+** - Advanced relational database
- **SQLAlchemy 2.0+** - Modern Python SQL toolkit and ORM
- **Alembic 1.13+** - Database migration tool
- **psycopg 3.1+** - PostgreSQL adapter for Python

### Authentication & Security
- **python-jose** - JWT token handling
- **bcrypt** - Password hashing
- **passlib** - Password hashing library
- **email-validator** - Email validation

### Caching & Background Tasks
- **Redis 5.0+** - In-memory data structure store (optional)
- **Background Tasks** - Async task processing

### Utilities
- **python-dotenv** - Environment variable management
- **httpx** - HTTP client for external APIs
- **reportlab** - PDF generation
- **python-multipart** - File upload support

### Development & Testing
- **pytest 8.0+** - Testing framework
- **Alembic** - Database migrations

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.11+**
- **PostgreSQL 16+** (with superuser access for schema creation)
- **Redis** (optional, for caching)
- **Environment Variables** configured (see `.env.example`)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/hms-backend
   cd hms-backend
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   
   # On Windows
   venv\Scripts\activate
   
   # On Linux/Mac
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   
   Create `.env` file (refer to `.env.example`):
   ```env
   # Database
   DATABASE_URL=postgresql+psycopg://user:password@localhost:5434/hms_db
   
   # Security
   SECRET_KEY=your-secret-key-here
   ACCESS_TOKEN_EXPIRE_MINUTES=60
   
   # Redis (optional)
   REDIS_URL=redis://localhost:6379/0
   
   # Email
   EMAIL_BACKEND=resend
   RESEND_API_KEY=your-resend-api-key
   EMAIL_FROM=noreply@yourdomain.com
   
   # Demo Mode
   DEMO_MODE=true
   DEMO_AUTO_REFRESH_ON_LOGIN=true
   
   # Super Admin
   SUPER_ADMIN_EMAIL=admin@platform.local
   SUPER_ADMIN_PASSWORD=your-secure-password
   ```

5. **Set up database**
   ```bash
   # Run migrations
   alembic upgrade head
   ```

6. **Initialize platform**
   ```bash
   # Initialize platform metrics and super admin
   python -m scripts.setup_platform --init-metrics --ensure-super-admin
   ```

7. **Seed demo data (optional)**
   ```bash
   # Seed demo data for testing
   python -m scripts.seed_demo_data --seed
   ```

8. **Start development server**
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

   The API will be available at:
   - API: `http://localhost:8000/api/v1`
   - Swagger UI: `http://localhost:8000/docs`
   - ReDoc: `http://localhost:8000/redoc`

### Building for Production

```bash
# Run migrations
alembic upgrade head

# Start production server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## ☁️ Deployment

### Option 1: Render (Recommended for Quick Setup)

1. Connect your GitHub repository to Render
2. Create a new Web Service
3. Pre-Deploy Command: `python -m scripts.setup_platform --init-metrics --ensure-super-admin`
3. Set build command: `pip install -r requirements.txt && alembic upgrade head`
4. Set start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables in Render dashboard
6. Deploy automatically on every push

**Benefits:**
- Automatic HTTPS
- PostgreSQL database included
- Zero-config deployment
- Preview deployments for PRs

### Option 2: Docker

1. **Build Docker image**
   ```bash
   docker build -t hms-backend .
   ```

2. **Run with Docker Compose**
   ```bash
   docker-compose up -d
   ```

### Option 3: AWS/GCP/Azure

1. **Set up PostgreSQL database**
   - Create managed PostgreSQL instance
   - Configure connection string

2. **Deploy application**
   - Use container services (ECS, Cloud Run, Container Apps)
   - Or use serverless (Lambda with API Gateway)
   - Configure environment variables
   - Set up health checks

3. **Set up Redis** (optional)
   - Managed Redis instance
   - Configure connection string

---

## 📁 Project Structure

```
hms-backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/        # API endpoint modules
│   │       │   ├── admin.py      # Demo data management
│   │       │   ├── admissions.py # IPD admission endpoints
│   │       │   ├── appointments.py # OPD appointment endpoints
│   │       │   ├── auth.py       # Authentication endpoints
│   │       │   ├── dashboard.py  # Dashboard metrics
│   │       │   ├── departments.py # Department management
│   │       │   ├── documents.py  # Document management
│   │       │   ├── patients.py   # Patient management
│   │       │   ├── platform_tenants.py # Platform management
│   │       │   ├── prescriptions.py # Prescription management
│   │       │   ├── roles.py      # Role management
│   │       │   ├── sharing.py    # Patient sharing
│   │       │   ├── stock_items.py # Stock management
│   │       │   ├── tenants.py    # Tenant registration
│   │       │   ├── users.py      # User management
│   │       │   └── vitals.py     # Vitals management
│   │       └── router.py          # API router configuration
│   ├── core/
│   │   ├── config.py            # Application configuration
│   │   ├── database.py          # Database connection
│   │   ├── dependencies.py     # FastAPI dependencies
│   │   ├── security.py         # Security utilities
│   │   ├── tenant_context.py   # Tenant context management
│   │   └── tenant_db.py        # Tenant database utilities
│   ├── models/                  # SQLAlchemy models
│   │   ├── patient.py          # Patient model
│   │   ├── appointment.py      # Appointment model
│   │   ├── prescription.py     # Prescription model
│   │   ├── admission.py        # Admission model
│   │   ├── user.py             # User model
│   │   ├── tenant_global.py    # Tenant model (public schema)
│   │   └── ...                 # Other models
│   ├── schemas/                 # Pydantic schemas
│   │   ├── patient.py          # Patient schemas
│   │   ├── appointment.py     # Appointment schemas
│   │   └── ...                 # Other schemas
│   ├── services/               # Business logic
│   │   ├── auth_service.py     # Authentication logic
│   │   ├── patient_service.py  # Patient business logic
│   │   ├── tenant_service.py   # Tenant management
│   │   └── ...                 # Other services
│   ├── notifications/          # Notification modules
│   │   ├── email/             # Email notifications
│   │   ├── sms/               # SMS notifications
│   │   └── whatsapp/          # WhatsApp notifications
│   ├── utils/                  # Utility functions
│   │   ├── email_templates.py # Email templates
│   │   ├── file_storage.py    # File storage utilities
│   │   └── ...                # Other utilities
│   └── main.py                 # FastAPI application
├── alembic/                     # Database migrations
│   ├── versions/               # Migration files
│   └── env.py                  # Alembic environment
├── scripts/                     # Utility scripts
│   ├── seed_demo_data.py       # Demo data seeding
│   ├── setup_platform.py       # Platform initialization
│   └── demo_data/             # Demo data JSON files
├── tests/                      # Test files
│   └── test_smoke.py          # Smoke tests
├── .env.example               # Environment variables template
├── alembic.ini                 # Alembic configuration
├── requirements.txt           # Python dependencies
└── pyproject.toml             # Project configuration
```

---

## 🔑 Key Features Deep Dive

### Multi-Tenant Architecture

The backend implements a **schema-per-tenant** architecture:

- **Public Schema**: Shared tables (users, tenants, permissions, patient_shares)
- **Tenant Schemas**: Isolated tables per tenant (patients, appointments, prescriptions, etc.)
- **Automatic Context Switching**: PostgreSQL `search_path` manages tenant context
- **Complete Isolation**: Each tenant's data is completely isolated
- **Scalable**: Easy to add new tenants without affecting existing ones

**Benefits:**
- Data isolation at database level
- Easy backup and restore per tenant
- Tenant-specific optimizations
- Compliance with data residency requirements

### Role-Based Access Control (RBAC)

**Permission System:**
- **Permission Definitions**: Stored in public schema
- **Role Permissions**: Assigned in tenant schema
- **Permission Codes**: Standardized codes (e.g., `patients:view`, `appointments:create`)
- **Permission Categories**: Organized by feature area

**Access Control:**
- **Endpoint Protection**: `require_permission()` decorator
- **Component-Level**: Permission checking in business logic
- **ABAC Support**: Attribute-based access control for complex rules

### Authentication Flow

1. **Login**: User provides email/password
2. **Authentication**: Backend validates credentials
3. **Token Issuance**: JWT access token issued
4. **Token Refresh**: Automatic refresh before expiration
5. **Tenant Context**: Tenant context extracted from user
6. **Permission Check**: Permissions validated per request

### Appointment Lifecycle

```
SCHEDULED → CHECKED_IN → IN_CONSULTATION → COMPLETED
     ↓                                           ↓
  NO_SHOW                                    (End)
     ↓
  CANCELLED
```

**Status Transitions:**
- Enforced workflow with validation
- Automatic timestamp recording
- Notification triggers at each stage
- Business rule validation

### Prescription Workflow

```
DRAFT → ISSUED → DISPENSED
  ↓
CANCELLED
```

**Features:**
- Stock integration (automatic deduction)
- Multi-item support
- Dosage management
- Print support (PDF generation)

### Patient Sharing

**Sharing Modes:**
- **Read-Write**: Full access to patient records
- **Read-Only Link**: Token-based secure link

**Security:**
- Secure token generation
- Expiration management
- Access logging
- Revocation support

### Demo Data Management

**Demo Operations:**
- **Seed**: Create comprehensive demo data
- **Freshen**: Shift dates forward (maintains relationships)
- **Reset**: Clear all demo data

**Demo Data Includes:**
- Two demo tenants (A and B)
- 9 staff users per tenant (admin, doctors, nurses, pharmacists, receptionists)
- 100 patients per tenant
- 500 appointments per tenant
- 200 IPD admissions per tenant
- 220 prescriptions per tenant
- Stock items and vitals

---

## 🔐 Security

### Authentication Security
- **JWT Tokens**: Secure token-based authentication
- **Password Hashing**: bcrypt with salt
- **Password History**: Prevent password reuse
- **Token Expiration**: Configurable token expiration
- **Email Verification**: Secure email verification

### Authorization Security
- **Permission-Based**: Granular permission checking
- **Role-Based**: Role-based access control
- **Tenant Isolation**: Complete tenant data isolation
- **Input Validation**: Pydantic schema validation
- **SQL Injection Prevention**: SQLAlchemy ORM protection

### Data Security
- **Encryption**: Sensitive data encryption
- **Secure File Storage**: Secure file upload handling
- **Audit Logging**: Track important operations
- **CORS Protection**: Configurable CORS origins

---

## 🧪 Development

### Available Scripts

```bash
# Run development server
uvicorn app.main:app --reload

# Run migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Run tests
pytest

# Seed demo data
python -m scripts.seed_demo_data --seed

# Freshen demo data
python -m scripts.seed_demo_data --freshen --freshen-days 7

# Reset demo data
python -m scripts.seed_demo_data --reset

# Setup platform
python -m scripts.setup_platform --init-metrics --ensure-super-admin
```

### Code Style

- Follow PEP 8 style guide
- Use type hints throughout
- Document functions and classes
- Write unit tests for new features
- Follow FastAPI best practices

### Environment Variables

Key environment variables (see `.env.example` for complete list):

**Required:**
- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - JWT secret key

**Optional:**
- `REDIS_URL` - Redis connection string (for caching)
- `EMAIL_BACKEND` - Email backend (smtp/resend)
- `DEMO_MODE` - Enable demo mode features
- `SUPER_ADMIN_EMAIL` - Super admin email
- `SUPER_ADMIN_PASSWORD` - Super admin password

---

## 📊 API Documentation

### Interactive Documentation

The API includes automatic interactive documentation:

- **Swagger UI**: Available at `/docs`
- **ReDoc**: Available at `/redoc`
- **OpenAPI Schema**: Available at `/openapi.json`

### API Endpoints

**Authentication:**
- `POST /api/v1/auth/login` - User login
- `GET /api/v1/auth/me` - Get current user
- `POST /api/v1/auth/forgot-password` - Request password reset
- `POST /api/v1/auth/reset-password` - Reset password
- `POST /api/v1/auth/change-password` - Change password

**Patients:**
- `GET /api/v1/patients` - List patients
- `POST /api/v1/patients` - Create patient
- `GET /api/v1/patients/{id}` - Get patient
- `PATCH /api/v1/patients/{id}` - Update patient

**Appointments:**
- `GET /api/v1/appointments` - List appointments
- `POST /api/v1/appointments` - Create appointment
- `PATCH /api/v1/appointments/{id}/check-in` - Check in
- `PATCH /api/v1/appointments/{id}/start-consultation` - Start consultation
- `PATCH /api/v1/appointments/{id}/complete` - Complete appointment

**Prescriptions:**
- `GET /api/v1/prescriptions` - List prescriptions
- `POST /api/v1/prescriptions` - Create prescription
- `PATCH /api/v1/prescriptions/{id}/status` - Update status

**And many more...** See Swagger UI for complete API documentation.

---

## 🗄️ Database

### Schema Structure

**Public Schema:**
- `users` - All users (platform-wide)
- `tenants` - Hospital tenants
- `permission_definitions` - Permission definitions
- `patient_shares` - Cross-tenant patient sharing
- `tenant_metrics` - Platform metrics

**Tenant Schema (per tenant):**
- `patients` - Patient records
- `appointments` - OPD appointments
- `prescriptions` - Prescriptions
- `admissions` - IPD admissions
- `vitals` - Vital signs
- `departments` - Departments
- `tenant_roles` - Roles
- `tenant_user_roles` - User-role assignments
- `stock_items` - Stock items
- `documents` - Patient documents

### Migrations

Database schema is managed with Alembic:

```bash
# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback migration
alembic downgrade -1
```

---

## 🔔 Notifications

### Email Notifications

**Backends:**
- **Resend**: Modern email API (recommended)
- **SMTP**: Traditional SMTP server

**Features:**
- HTML email templates
- Email sandbox mode (testing)
- Email logging
- Configurable sender

### SMS Notifications

**Providers:**
- **Twilio**: SMS provider integration
- **Extensible**: Easy to add new providers

**Features:**
- SMS to patients (with consent)
- SMS to staff
- Configurable SMS provider

---

## 🧪 Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app

# Run specific test file
pytest tests/test_smoke.py
```

### Test Structure

- **Smoke Tests**: Basic API functionality
- **Integration Tests**: End-to-end workflows
- **Unit Tests**: Individual function testing

---

## 📈 Performance Optimizations

- **Database Indexing**: Optimized indexes for queries
- **Query Optimization**: Efficient SQLAlchemy queries
- **Connection Pooling**: Database connection pooling
- **Redis Caching**: Optional Redis caching
- **Lazy Loading**: Efficient relationship loading
- **Pagination**: Efficient pagination for large datasets

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Write Python with type hints
- Follow PEP 8 style guide
- Add tests for new features
- Update API documentation
- Ensure migrations are backward compatible
- Test with multiple tenants

---

## 📝 License

This project is licensed under the MIT License. See the `LICENSE` file for details.

---

## 🙏 Acknowledgments

- Built with FastAPI for high performance
- Uses PostgreSQL for robust data management
- Multi-tenant architecture for scalability
- Type-safe development with Pydantic
- Modern Python best practices

---

## 📞 Support

For issues, questions, or contributions, please open an issue on GitHub.

---

## 🔒 Security & Code Quality

- **Automatic Security Updates:** Dependencies monitored and updated regularly with Dependabot.
- **Vulnerability Audits:** Run `pip-audit` and `safety` in CI to catch vulnerable packages.
- **Environment Variables:** Sensitive configurations loaded via environment variables.
- **Input Validation:** All endpoints validate and sanitize inputs using Pydantic models.
- **Authentication:** Secure JWT authentication with token expiry and refresh.
- **Authorization:** Role-based access enforced at the route and resource level.
- **Secure Headers:** HTTP security headers provided via FastAPI middleware.
- **Error Handling:** Custom exception handlers avoid information leakage.
- **Testing:** 100% code coverage target, with automated unit/integration tests.
- **Linting & Formatting:** Uses `ruff`, `black`, and `isort` on every commit/PR.
- **Static Analysis:** Type-checking (`mypy`) and vulnerability scanning in CI.
- **Secrets Management:** No secrets hard-coded; use `.env` files or cloud secret managers.

Please see [SECURITY.md](SECURITY.md) for full practices and responsible disclosure process.


**Last Updated:** December 2025