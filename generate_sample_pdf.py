from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

def create_sample_pdf(filename="CEO_Executive_Strategy_2026.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=12
    )

    body_style = styles['Normal']

    # Header
    story.append(Paragraph("<b>ORG X - EXECUTIVE STRATEGY & ROADMAP 2026</b>", title_style))
    story.append(Paragraph("<b>Classification:</b> SECRET | <b>Clearance Required:</b> Level 4 (CEO Office)", body_style))
    story.append(Spacer(1, 15))

    # Executive Summary
    story.append(Paragraph("<b>1. Strategic Objectives</b>", styles['Heading2']))
    story.append(Paragraph(
        "In Q4 2026, Org X plans to expand cloud infrastructure expenditure by 35% to support "
        "real-time Agentic RAG services. Project 'Aegis' will focus on migrating legacy MongoDB clusters "
        "to multi-region sharded deployments.",
        body_style
    ))
    story.append(Spacer(1, 15))

    # Budget Table
    story.append(Paragraph("<b>2. Q4 Budget Allocations</b>", styles['Heading2']))
    data = [
        ["Department", "Project Name", "Allocated Budget", "Clearance Needed"],
        ["Infrastructure", "MongoDB Sharding", "$1,200,000", "Level 3"],
        ["AI Engineering", "Agentic RAG R&D", "$2,500,000", "Level 2"],
        ["Executive Security", "Project Aegis M&A", "$5,000,000", "Level 4"]
    ]
    
    t = Table(data, colWidths=[100, 140, 110, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1A365D")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey)
    ]))
    story.append(t)

    doc.build(story)
    print(f"Generated {filename} successfully.")

if __name__ == "__main__":
    create_sample_pdf()