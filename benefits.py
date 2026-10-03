
from flask import Blueprint, render_template

benefits_bp = Blueprint(
    "benefits",
    __name__,
    url_prefix="/benefits"
)

@benefits_bp.route("/")
def benefits_home():
    schemes = [
        {
            "title": "Scholarship Support",
            "category": "Education",
            "description": "Financial assistance for eligible students.",
            "eligibility": "Students meeting the scheme criteria.",
            "icon": "🎓",
            "status": "Demo"
        },
        {
            "title": "Women Empowerment",
            "category": "Women",
            "description": "Support programs for eligible women.",
            "eligibility": "As per scheme guidelines.",
            "icon": "👩",
            "status": "Demo"
        },
        {
            "title": "Pension Support",
            "category": "Pension",
            "description": "Financial support programs for eligible citizens.",
            "eligibility": "As per scheme guidelines.",
            "icon": "👴",
            "status": "Demo"
        },
        {
            "title": "Housing Assistance",
            "category": "Housing",
            "description": "Housing support for eligible households.",
            "eligibility": "As per scheme guidelines.",
            "icon": "🏠",
            "status": "Demo"
        },
        {
            "title": "Employment Support",
            "category": "Employment",
            "description": "Programs supporting skills and employment.",
            "eligibility": "As per scheme guidelines.",
            "icon": "💼",
            "status": "Demo"
        },
        {
            "title": "Healthcare Assistance",
            "category": "Healthcare",
            "description": "Healthcare support programs for eligible citizens.",
            "eligibility": "As per scheme guidelines.",
            "icon": "🏥",
            "status": "Demo"
        }
    ]

    return render_template("benefits.html", schemes=schemes)