# eLiberia Backend

A robust backend API for the eLiberia project built with **Python** and **Flask**.

## Tech Stack

- **Language**: Python 3.8+
- **Framework**: Flask
- **Database**: SQLite (Development) / PostgreSQL (Production)
- **Package Manager**: pip

## Project Structure

```
app/
├── models/       # Database models
├── routes/       # API endpoints
├── services/     # Business logic
├── utils/        # Utility functions
└── middleware/   # Custom middleware

tests/            # Unit and integration tests
requirements.txt  # Python dependencies
dev_server.py     # Development server entry point
init_db.py        # Database initialization
```

## Getting Started

### Prerequisites
- Python 3.8+
- pip (Python package manager)
- PostgreSQL (optional, for production)

### Installation

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Environment Setup

Create a `.env` file in the root directory:

```
FLASK_ENV=development
FLASK_APP=app.py
DATABASE_URL=sqlite:///eliberia_dev.db
SECRET_KEY=your-secret-key-here
DEBUG=True
```

### Database Setup

```bash
# Initialize database
python init_db.py

# Run migrations (if applicable)
python -m flask db upgrade
```

### Development

```bash
# Start development server
python dev_server.py
```

The API will be available at `http://localhost:5000`

### Testing

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=app
```

## API Documentation

API documentation will be available at `http://localhost:5000/api/docs` (if Swagger is configured)

## Contributing

1. Create a feature branch (`git checkout -b feature/amazing-feature`)
2. Commit your changes (`git commit -m 'Add amazing feature'`)
3. Push to the branch (`git push origin feature/amazing-feature`)
4. Open a Pull Request

## License

This project is part of Global Impact Innovators Ltd (GIIN)

## Support

For issues and questions, please open an issue in the repository.
