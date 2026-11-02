# Sample CV PDF - Generation Instructions

The `sample_cv.pdf` file is used by E2E and integration tests. This file should be a realistic PDF containing a sample resume/CV.

## Quick Start

**Option 1: Create manually** (Recommended for realism)

1. Open your favorite word processor (Google Docs, MS Word, LibreOffice Writer)
2. Create a sample CV/resume with the following structure:
   - **Header**: Name, email, phone, location
   - **Professional Summary**: 2-3 sentences
   - **Work Experience**: 2-3 positions with dates and achievements
   - **Education**: Bachelor's degree in Computer Science or related field
   - **Skills**: Programming languages (Python, TypeScript, JavaScript), frameworks, tools
3. Export/Save as PDF: `sample_cv.pdf`
4. Place the file in `Solution/tests/fixtures/profiles/sample_cv.pdf`
5. Commit to Git

## Suggested CV Content

### Header
```
MARCUS CHEN
Email: marcus.chen@example.com | Phone: +49 6131 123456 | Location: Kassel, Germany
LinkedIn: linkedin.com/in/marcuschen
```

### Professional Summary
```
Experienced software engineer with 6+ years of experience designing and developing 
full-stack applications. Passionate about leveraging AI/ML to solve real-world problems. 
Proven track record of delivering scalable, maintainable solutions in fast-paced environments.
```

### Work Experience
```
Senior Software Engineer | TechCorp GmbH | January 2021 - Present
- Architected and led the development of AI-powered document processing service using FastAPI and OpenAI GPT
- Designed and built React/Next.js frontend serving 10,000+ monthly active users
- Implemented comprehensive test suite (unit, integration, E2E) achieving 85% code coverage
- Mentored 2 junior engineers and contributed to architecture decisions

Full Stack Developer | StartupXYZ | June 2018 - December 2020
- Developed REST APIs in Python (Flask, SQLAlchemy) serving 1M+ requests/month
- Built responsive React applications with TypeScript for internal and customer-facing tools
- Set up CI/CD pipelines using GitHub Actions and Docker

Software Developer | DataSystems Inc. | July 2017 - May 2018
- Implemented database optimization improving query performance by 40%
- Contributed to backend codebase (Python, PostgreSQL)
```

### Education
```
Bachelor of Science in Computer Science
Technical University of Darmstadt | 2017
```

### Skills
```
Languages: Python, TypeScript, JavaScript, SQL, Bash
Frameworks & Libraries: FastAPI, React, Next.js, SQLAlchemy, Pydantic
Tools & Platforms: Docker, GitHub, PostgreSQL, Git, VS Code
Concepts: REST APIs, Microservices, OOP, Agile, TDD, CI/CD
```

## Option 2: Generate Programmatically

If you prefer to generate the PDF programmatically (e.g., using reportlab in Python), create a script:

```python
# generate_sample_cv.py
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# Create PDF
pdf_file = "Solution/tests/fixtures/profiles/sample_cv.pdf"
doc = SimpleDocTemplate(pdf_file, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)

# Define styles
styles = getSampleStyleSheet()
title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], fontSize=14, textColor='black', alignment=TA_CENTER, spaceAfter=6)
heading_style = ParagraphStyle(name='Heading', parent=styles['Heading2'], fontSize=11, textColor='black', spaceAfter=6)
body_style = ParagraphStyle(name='Body', parent=styles['BodyText'], fontSize=10, spaceAfter=3)

# Build content
content = [
    Paragraph("MARCUS CHEN", title_style),
    Paragraph("marcus.chen@example.com | +49 6131 123456 | Kassel, Germany", body_style),
    Spacer(1, 0.2*inch),
    Paragraph("PROFESSIONAL SUMMARY", heading_style),
    Paragraph("Experienced software engineer with 6+ years developing full-stack applications.", body_style),
    # ... Add more content ...
]

# Build PDF
doc.build(content)
print(f"✓ CV generated: {pdf_file}")
```

Then run: `python generate_sample_cv.py`

## Checklist Before Committing

- [ ] PDF file is valid and readable (opens in your PDF viewer)
- [ ] File is named exactly: `sample_cv.pdf`
- [ ] File is placed in: `Solution/tests/fixtures/profiles/sample_cv.pdf`
- [ ] Content includes: name, contact, experience, education, skills
- [ ] File size is reasonable: 50KB - 500KB (not too large, not too small)
- [ ] PDF is not encrypted or password-protected

## Notes

- The CV content should be realistic but anonymized (no real personal information)
- The content should align with the sample job description in `JDs/sample_jd.txt`
- Consider creating additional CVs with different backgrounds to test edge cases in future sprints
- Keep the file updated if the application's CV parsing logic changes significantly

Once created, commit the file to Git:
```bash
git add Solution/tests/fixtures/profiles/sample_cv.pdf
git commit -m "Add sample CV fixture for E2E tests"
git push
```
