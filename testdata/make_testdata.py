#!/usr/bin/env python3
"""Build a fake home folder for the file-manager search demo.

Run the script:
    core/.venv/bin/python testdata/make_testdata.py testdata/home

The script deletes the output folder, builds it from scratch, and writes
testdata/queries.json next to this file.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

SCRIPT_DIR = Path(__file__).resolve().parent
PANDOC = "pandoc"
PAGE_W, PAGE_H = 595, 842
MARGIN = 56

# ---------------------------------------------------------------- mtimes
# Spread modification times over the last 18 months (~540 days).
_DAY_OFFSETS = list(range(1, 541))
random.Random(20240301).shuffle(_DAY_OFFSETS)
_mtime_index = 0


def stamp_mtime(path: Path) -> None:
    """Set a distinct modification time on one path."""
    global _mtime_index
    _mtime_index += 1
    days = _DAY_OFFSETS[_mtime_index % len(_DAY_OFFSETS)]
    rnd = random.Random(90000 + _mtime_index)
    when = time.time() - days * 86400 - rnd.randint(0, 86399)
    os.utime(path, (when, when))


# ---------------------------------------------------------------- writers
_scratch_root: Path | None = None


def scratch() -> Path:
    """Return the scratch folder for temporary files."""
    global _scratch_root
    if _scratch_root is None:
        _scratch_root = Path(tempfile.mkdtemp(prefix="make_testdata_"))
    return _scratch_root


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    stamp_mtime(path)
    return path


def write_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    stamp_mtime(path)
    return path


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    body = "\n".join(",".join(row) for row in rows) + "\n"
    return write_text(path, body)


def _draw_paragraphs(page: pymupdf.Page, text: str, fontsize: float = 10.5) -> None:
    """Place text on one page. Shrink the font until the text fits."""
    rect = pymupdf.Rect(MARGIN, MARGIN, PAGE_W - MARGIN, PAGE_H - MARGIN)
    size = fontsize
    while size > 5:
        rc = page.insert_textbox(rect, text, fontsize=size, fontname="helv", align=0)
        if rc >= 0:
            return
        size -= 0.5


def noise_png(path: Path, side: int) -> Path:
    """Write an incompressible PNG. The file grows the host PDF."""
    data = os.urandom(side * side * 3)
    img = Image.frombytes("RGB", (side, side), data)
    img.save(path, "PNG", compress_level=0)
    return path


def make_pdf(path: Path, pages: list[str], noise_side: int = 0) -> Path:
    """Write a PDF. A noise_side above zero adds one large image page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    if noise_side:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        tmp = noise_png(scratch() / f"noise_{noise_side}_{path.stem}.png", noise_side)
        page.insert_image(page.rect, filename=str(tmp))
    for text in pages:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        _draw_paragraphs(page, text)
    doc.save(str(path), deflate=True)
    doc.close()
    stamp_mtime(path)
    return path


def make_big_pdf(path: Path, pages: list[str], floor: int = 2 * 1024 * 1024) -> Path:
    """Write a PDF larger than the floor size in bytes."""
    for side in (900, 1300, 1800, 2400):
        make_pdf(path, pages, noise_side=side)
        if path.stat().st_size >= floor:
            return path
    raise RuntimeError(f"{path} stays below {floor} bytes")


def make_docx(path: Path, markdown_text: str) -> Path:
    """Convert markdown to DOCX with the pandoc binary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    src = scratch() / f"{path.stem}_{_mtime_index}.md"
    src.write_text(markdown_text, encoding="utf-8")
    result = subprocess.run(
        [PANDOC, str(src), "-o", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pandoc failed for {path}: {result.stderr.strip()}")
    stamp_mtime(path)
    return path


def make_image(path: Path, kind: str, rnd: random.Random) -> Path:
    """Draw one simple image with Pillow."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 640, 480
    img = Image.new("RGB", (w, h), (250, 250, 248))
    d = ImageDraw.Draw(img)

    if kind == "sunset":
        for y in range(h):
            t = y / (h - 1)
            color = (
                int(255 - 10 * t),
                int(150 - 120 * t),
                int(90 + 90 * t),
            )
            d.line([(0, y), (w, y)], fill=color)
        d.ellipse([w - 220, 60, w - 80, 200], fill=(255, 236, 140))
        d.polygon([(0, h), (180, 300), (360, h)], fill=(60, 40, 70))
        d.polygon([(260, h), (460, 260), (660, h)], fill=(45, 30, 55))
    elif kind == "circles":
        for i in range(9):
            r = 200 - i * 22
            d.ellipse([w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r],
                      outline=(30 + i * 20, 80, 200 - i * 15), width=6)
    elif kind == "flow":
        boxes = [(60, 60), (270, 60), (60, 260), (270, 260)]
        for x, y in boxes:
            d.rectangle([x, y, x + 180, y + 90], outline=(40, 90, 160), width=4,
                        fill=(225, 238, 252))
        d.line([(240, 105), (270, 105)], fill=(40, 90, 160), width=4)
        d.line([(240, 305), (270, 305)], fill=(40, 90, 160), width=4)
        d.line([(150, 150), (150, 260)], fill=(40, 90, 160), width=4)
        d.line([(360, 150), (360, 260)], fill=(40, 90, 160), width=4)
    elif kind == "chart":
        d.line([(60, 420), (600, 420)], fill=(80, 80, 80), width=3)
        d.line([(60, 40), (60, 420)], fill=(80, 80, 80), width=3)
        for i, value in enumerate((120, 210, 90, 300, 250, 170)):
            x = 90 + i * 80
            d.rectangle([x, 420 - value, x + 50, 420],
                        fill=(60 + i * 25, 170 - i * 12, 120), outline=(40, 40, 40))
    elif kind == "checker":
        tile = 40
        for gy in range(0, h, tile):
            for gx in range(0, w, tile):
                if (gx // tile + gy // tile) % 2 == 0:
                    d.rectangle([gx, gy, gx + tile, gy + tile], fill=(235, 240, 245))
                else:
                    d.rectangle([gx, gy, gx + tile, gy + tile], fill=(120, 140, 165))
    elif kind == "avatar":
        d.ellipse([170, 60, 470, 360], fill=(90, 150, 210))
        d.ellipse([250, 140, 390, 280], fill=(250, 235, 210))
        d.rectangle([200, 320, 440, 480], fill=(60, 110, 170))
    elif kind == "screenshot":
        d.rectangle([40, 40, 600, 440], outline=(70, 70, 90), width=4, fill=(252, 252, 254))
        d.rectangle([40, 40, 600, 90], fill=(70, 70, 90))
        for i in range(4):
            y = 130 + i * 60
            d.rectangle([80, y, 560, y + 30], outline=(160, 170, 185), fill=(240, 244, 250))
            d.rectangle([80, y + 45, 220, y + 62], fill=(90, 130, 200))
    elif kind == "lines":
        for _ in range(24):
            d.line(
                [(rnd.randint(0, w), rnd.randint(0, h)),
                 (rnd.randint(0, w), rnd.randint(0, h))],
                fill=(rnd.randint(20, 220), rnd.randint(20, 220), rnd.randint(20, 220)),
                width=rnd.randint(1, 6),
            )
    elif kind == "tiles":
        for gy in range(0, h, 60):
            for gx in range(0, w, 60):
                shade = (gx + gy) % 200
                d.rectangle([gx + 6, gy + 6, gx + 54, gy + 54],
                            fill=(60 + shade // 2, 120 + shade // 3, 200 - shade // 2))
    else:
        raise ValueError(kind)

    if path.suffix.lower() in (".jpg", ".jpeg"):
        img.save(path, "JPEG", quality=88)
    else:
        img.save(path, "PNG")
    stamp_mtime(path)
    return path


# ---------------------------------------------------------------- content
LEASE_PAGE_1 = """DEED OF LEASE

This deed of lease is made at Pune on 12 March 2024 between the lessor and the lessee named below.

LESSOR: Mr. Anil Deshmukh, Flat 3B, Sahyadri Residency, Kothrud, Pune 411038.

LESSEE: Ms. Priya Nair, 21 MG Road, Camp, Pune 411001.

The lessor gives the premises described below to the lessee for a period of 22 months, from 1 April 2024 to 31 January 2026.

PREMISES: Flat 704, Tower B, Gokul Heights, Baner Road, Pune 411045. Two bedrooms, two bathrooms and one covered car park.

USE: The lessee uses the premises for residential purposes only.

FINANCIAL TERMS

1. Monthly payment: Rs 28,500 (twenty eight thousand five hundred rupees). The lessee pays this sum on or before the 5th day of each calendar month.

2. Security deposit: Rs 85,000 (eighty five thousand rupees). The lessor keeps the deposit for the full period of the tenancy and returns it within 30 days after the lessee vacates the premises.

3. Late fee: if the monthly payment arrives after the 5th day of the month, the lessee pays a late fee of Rs 500 for each day of delay.

4. Utilities: the lessee pays the electricity bill, the water bill and the society charges.

5. The lessor pays the property tax and the major repairs."""

LEASE_PAGE_2 = """MAINTENANCE AND CONDUCT

6. The lessee keeps the premises clean and in good order.

7. The lessee does not make structural changes to the premises without the written consent of the lessor.

8. The lessee does not sublet the premises or any part of the premises.

9. The lessor may inspect the premises with 24 hours notice, between 10:00 and 18:00.

10. The lessee does not keep any pet in the premises without the written consent of the lessor.

NOTICE AND END OF TENANCY

11. Notice period: either side gives 60 days written notice to end this tenancy.

12. If the lessee vacates before the end of the notice period, the lessor may adjust the pending monthly payment from the security deposit.

13. On the last day of the tenancy the lessee returns all keys and the car park card to the lessor.

SIGNATURES

Lessor: Anil Deshmukh

Lessee: Priya Nair

Witness 1: R. Kulkarni

Witness 2: S. Iyer"""


def lease_text() -> str:
    text = LEASE_PAGE_1 + "\n" + LEASE_PAGE_2
    low = text.lower()
    for bad in ("rent", "agreement"):
        if bad in low:
            raise RuntimeError(f"the lease text holds the forbidden word {bad!r}")
    return text


INVOICE_WEB = """# Tax Invoice

**Invoice number:** INV-8812
**Date:** 18 January 2024
**Seller:** Nimbus Web Studio, 4th Floor, Icon Tower, Baner, Pune 411045
**GSTIN:** 27ABCDE1234F1Z5

**Buyer:** Kaveri Foods Pvt Ltd, Hadapsar Industrial Estate, Pune 411013

## Work done

| Item | Quantity | Rate | Amount |
| --- | --- | --- | --- |
| Website design, five pages | 1 | 45,000 | 45,000 |
| Product catalogue module | 1 | 22,000 | 22,000 |
| Hosting setup for one year | 1 | 6,500 | 6,500 |

Subtotal: 73,500
CGST 9%: 6,615
SGST 9%: 6,615
**Total payable: 86,730**

Payment is due within 15 days. Transfer the amount to the current account named below.

Bank: HDFC Bank, Baner branch
Account: 50190033445566
IFSC: HDFC0001234

Thank you for your business.
"""

CV_TEXT = """PRIYA NAIR
Backend Developer
priya.nair@example.com | +91 90000 11122 | Pune, India

SUMMARY
Backend developer with four years of work on Python services and relational databases. I build REST APIs, tune SQL queries and run deployments on Linux servers.

SKILLS
- Languages: Python, SQL, JavaScript
- Frameworks: Django, FastAPI, Flask
- Databases: PostgreSQL, MySQL, SQLite
- Tools: Docker, Git, Redis, Celery

EXPERIENCE
Backend Developer, Lumen Retail Systems, Pune (2022 to present)
- Built the order service in Django and cut the average response time from 900 ms to 210 ms.
- Wrote a nightly Celery job that reconciles 1.2 million rows in PostgreSQL.
- Added 240 unit tests and raised the coverage from 41 percent to 88 percent.

Junior Developer, Tangle Software, Pune (2020 to 2022)
- Maintained a Flask billing API for 60 retail shops.
- Moved the reporting tables to indexed views.

EDUCATION
B.E. Computer Engineering, Savitribai Phule Pune University, 2020. First class with distinction.

PROJECTS
- Ledger API: double entry accounting service in FastAPI with PostgreSQL and SQLAlchemy.
- Crawler Kit: small scraping toolkit with asyncio and Redis queues.
"""

GOA_TRIP = """# Seven day trip plan: Goa

Travel dates: 9 February 2024 to 16 February 2024. Two travellers.

## Flights
- 9 Feb, 6U 512, Pune to Goa, depart 06:40, arrive 08:05.
- 16 Feb, 6U 519, Goa to Pune, depart 19:20, arrive 20:50.

## Stay
- Beach guest house at Candolim, deluxe room, seven nights, breakfast included.

## Day plan
1. Day 1: land at Dabolim, taxi to Candolim, evening walk on the beach.
2. Day 2: Fort Aguada and the lighthouse, lunch at a shack near Sinquerim.
3. Day 3: boat trip to Grande Island, snorkelling in the afternoon.
4. Day 4: Old Goa churches, then the Latin quarter at Fontainhas.
5. Day 5: scooter ride to Anjuna and Vagator, sunset at Chapora fort.
6. Day 6: spice garden near Ponda, then a river cruise at Panjim.
7. Day 7: shopping at the Saturday night market, then the flight home.

## Budget
- Flights: 11,400
- Stay: 21,000
- Food and travel: 9,000

## Packing list
Sunglasses, sunscreen, two swimsuits, a light raincoat, power bank and a copy of the flight tickets.
"""

PAV_BHAJI = """# Pav bhaji, the Mumbai way

Serves four people. Total time about 50 minutes.

## Ingredients
- 4 potatoes, boiled and mashed
- 1 cup green peas
- 2 onions, chopped fine
- 3 tomatoes, chopped
- 1 capsicum, chopped
- 2 tablespoons pav bhaji masala
- 1 teaspoon red chilli powder
- 40 g butter
- 8 pav buns
- Salt and lemon to taste

## Method
1. Melt half the butter in a wide pan.
2. Fry the onions until they turn golden.
3. Add the capsicum and cook for three minutes.
4. Add the tomatoes and cook until the mixture turns soft.
5. Add the masala, the chilli powder and salt.
6. Add the mashed potatoes and the peas. Mash everything together for five minutes.
7. Add a cup of water and simmer for ten minutes.
8. Finish with the rest of the butter and a squeeze of lemon.
9. Toast the pav buns with butter and serve hot with chopped onion.

Tip: a heavy iron pan gives a better texture than a non stick pan.
"""

BANK_STATEMENT = """HDFC BANK LIMITED
Statement of account
Branch: Baner, Pune 411045

Account holder: PRIYA NAIR
Account number: 50190033445566
IFSC: HDFC0001234
Customer ID: 88120041
PAN: ABCDE1234F
Aadhaar: 4321 8765 2109
Statement period: 1 March 2024 to 31 March 2024

Opening balance: 1,42,880.55

Date        Particulars                     Withdrawal    Deposit     Balance
01-03-2024  UPI to Green Grocers            1,240.00                  1,41,640.55
03-03-2024  Salary credit, Lumen Retail                   68,400.00   2,10,040.55
04-03-2024  Electricity bill, MSEDCL          2,180.00                 2,07,860.55
07-03-2024  Mobile bill, Bharti Airtel          999.00                 2,06,861.55
09-03-2024  UPI to Cafe Dario                  640.00                 2,06,221.55
11-03-2024  Mutual fund, SIP                   5,000.00                 2,01,221.55
12-03-2024  Cash withdrawal, ATM Baner         4,000.00                 1,97,221.55
15-03-2024  House maintenance                  8,500.00                 1,88,721.55
18-03-2024  Online order, e-store              2,349.00                 1,86,372.55
21-03-2024  UPI to Kiran Joshi                 3,000.00                 1,83,372.55
24-03-2024  Petrol, HP pump                    2,000.00                 1,81,372.55
27-03-2024  Interest credit                                  412.30     1,81,784.85
30-03-2024  DTH recharge                         330.00                 1,81,454.85

Closing balance: 1,81,454.85

This is a computer generated statement. Registered office: HDFC Bank House, Senapati Bapat Marg, Mumbai 400013.
"""

PHONE_BILL = """Bharti Airtel Limited
Postpaid mobile bill

Bill number: 4471-882-0031
Bill date: 5 April 2024
Due date: 25 April 2024

Name: Priya Nair
Mobile number: +91 90000 11122
Plan: Infinity 599 Family
Billing period: 1 March 2024 to 31 March 2024

Charges
- Monthly plan charge: 599.00
- Data add on, 30 GB: 151.00
- International roaming, one day: 199.00
- Late payment fee, earlier bill: 50.00

Taxable amount: 999.00
GST at 18 percent: 179.82
Total amount payable: 1,178.82

Usage in this period
- Voice calls: 412 minutes
- Data used: 44.6 GB of 60 GB
- SMS: 88

Pay before the due date to stop the disconnection of outgoing services.
"""

HW_DBMS = """DBMS home work 1
Topic: normalization and normal forms
Due: 12 February 2024

1. A relation R(student_id, student_name, course_id, course_name, teacher_id, teacher_name) holds many repeated course names. Show the partial dependencies and the transitive dependencies inside R.

2. Put R into second normal form. Give the tables and the keys after the change.

3. Put the result into third normal form. Explain each step.

4. A payment table holds (invoice_no, invoice_date, item_code, item_price, quantity). Find the functional dependencies and the single candidate key.

5. State one case where a table in BCNF is not in fourth normal form. Give a small example with rows.

6. Name two costs of a fully normalized design in a read heavy system.

7. Explain the difference between a lossless join decomposition and a dependency preserving decomposition.

Submit the answers as a single PDF. Show the tables in the standard notation with the primary key underlined.
"""

ELECTRICITY_BILL = """Maharashtra State Electricity Distribution Co. Ltd.
Tax invoice for electricity

Consumer name: Anil Deshmukh
Consumer number: 1900 4471 2288
Billing unit: Baner, Pune
Tariff: LT I residential

Meter number: PM-8812-4471
Meter reading date: 28 March 2024
Previous reading: 41,220 units
Present reading: 41,438 units
Units used: 218

Charges
- Energy charge: 1,042.20
- Fixed charge: 130.00
- Wheeling charge: 1,242.60
- Electricity duty: 241.68
- Meter charge: nil

Total amount payable: 2,656.48

Pay before 15 April 2024. A delayed payment attracts a late fee of 2 percent for each month.
"""

TRAIN_TICKET = """Indian Railways, e-ticket
IRCTC, authorised ticketing agent

PNR: 4471882290
Train: 12130 Azad Hind Express
Class and coach: 3A, coach B2
Berth: 21 lower
Ticket status: confirmed

From: Pune Junction (PUNE)
To: New Delhi (NDLS)
Boarding date: 22 February 2024
Departure: 23:10
Arrival: 04:35 on 24 February 2024

Passengers
1. Priya Nair, 29, female, berth 21 lower
2. Kiran Nair, 55, male, berth 22 middle

Fare paid: 3,120.00
Booking date: 4 February 2024

Carry a printed copy of this ticket with a photo identity card. Reach the platform at least 30 minutes before the departure.
"""

ORDER_CONFIRM = """Order confirmation

Order number: 404-8812233-1190044
Order date: 18 March 2024

Item: Wireless over ear headphones, black
Quantity: 1
Price: 2,349.00
Sold by: Audio Depot

Delivery address: Flat 704, Gokul Heights, Baner Road, Pune 411045
Expected delivery: 21 March 2024
Payment method: net banking

Status: shipped. Tracking number 8871 2204 9931.
"""

SHOPPING_LIST = """# To buy this week

- Milk, two litres
- Rice, five kilograms
- Onions and tomatoes
- Coriander and curry leaves
- Bread and eggs
- Dish wash liquid
- Toothpaste
- Note book, ruled, 200 pages
- Charging cable, type C

Also: pay the water bill, and book the gas cylinder.
"""

MEETING_NOTES = """Notes from the sprint meeting, 14 February 2024, 10:00 to 11:10.

Present: Priya, Nikhil, Farah, Omkar.

Decisions
1. The search page ships on 28 February.
2. Nikhil owns the indexer. Farah owns the user interface.
3. Omkar moves the old records to the new schema before the release.

Open items
- The embedding model needs a size check on the server.
- Farah asks for the final strings by Monday.
- The staging server holds no test data. Priya will build a sample folder.

Next meeting: 21 February 2024, 10:00.
"""

WIFI_NOTES = """Home network notes

Router: TP-Link AX1500
Admin page: http://192.168.0.1
Admin password: gullu-river-4417
WiFi name (2.4 GHz): Gokul_Home
WiFi password: mango-chutney-22
WiFi name (5 GHz): Gokul_Home_5G
WiFi password: mango-chutney-22-5

Printer: HP LaserJet on 192.168.0.42
NAS box: 192.168.0.55, user: home, password: box-o-rice

Guest network is off. Turn it on only for visitors.
"""

SYLLABUS = """Savitribai Phule Pune University
Third year B.E. Computer Engineering, semester 5
Scheme 2019, syllabus for four subjects

Database Management Systems (310241)
Unit 1: Introduction, data models, entity relationship design.
Unit 2: Relational algebra, SQL, joins, views.
Unit 3: Indexing, B trees, B+ trees, hashing.
Unit 4: Normalization, normal forms, decomposition.
Unit 5: Transactions, ACID properties, concurrency control, recovery.

Operating Systems (310242)
Unit 1: Processes, threads, system calls.
Unit 2: CPU scheduling, synchronization, semaphores.
Unit 3: Memory management, paging, virtual memory.
Unit 4: Deadlocks, detection and avoidance.
Unit 5: File systems and disk scheduling.

Computer Networks (310243)
Unit 1: Layered models, physical layer.
Unit 2: Data link layer, framing, error control.
Unit 3: Media access, Ethernet, switching.
Unit 4: Network layer, routing, IP addressing, subnetting.
Unit 5: Transport layer, TCP, congestion control.
Unit 6: Application layer, DNS, HTTP, SMTP.

Theory of Computation (310244)
Unit 1: Finite automata.
Unit 2: Regular expressions and languages.
Unit 3: Context free grammars, pushdown automata.
Unit 4: Turing machines and decidability.

Practical work: one laboratory journal for each subject, submitted before 20 April 2024.
"""

HALL_TICKET = """Examination hall ticket, semester 5, October 2024

Name: Priya Nair
Seat number: 5C-118
Course: Computer Engineering
College: Gokul Institute of Technology, Pune

Examination centre: Block A, third floor, room 312
Reporting time: 10:00. Examination window: 10:30 to 13:30.

Papers
- 18 October 2024: Database Management Systems, room 312
- 21 October 2024: Operating Systems, room 312
- 24 October 2024: Computer Networks, room 314
- 28 October 2024: Theory of Computation, room 314

Carry this hall ticket and one photo identity card to each paper. Mobile phones are not allowed in the examination room.
"""

COVER_LETTER = """# Cover letter

To the hiring manager
Lumen Retail Systems
Baner, Pune

18 January 2024

Dear Sir or Madam,

I apply for the post of backend developer at Lumen Retail Systems. I hold a degree in computer engineering and I have four years of work on Python services.

In my present role I built the order service in Django and cut the response time of the checkout path by about 75 percent. I also wrote the nightly reconciliation job that handles more than a million rows in PostgreSQL. I like clean data models, indexed queries and tests that catch faults before the release.

Your team works on retail platforms at a large scale. I want to bring my work on Django, PostgreSQL and Celery to that team.

I attach my curriculum vitae and I am free for an interview at your convenience.

Yours faithfully,
Priya Nair
"""

PROJECT_REPORT = """# Mini project report

## Title: Library book tracker

Submitted by: Priya Nair, 5C-118
Guide: Prof. M. Kulkarni
Course: Computer Engineering, semester 5

## Abstract

The library book tracker holds the catalog of a small college library. The software records the books, the members and the loans. A member can search the catalog by title, by author or by subject.

## Aim

Build a web tool that replaces the paper register of a library.

## Method

1. Studied the present register and listed the data fields.
2. Designed the tables: book, member, loan, fine.
3. Built the service in Django with SQLite for the first release.
4. Wrote tests for the search and for the fine rules.
5. Ran the tool on 1,200 sample books.

## Result

The search finds a book in under 100 ms. The fine rule works for weekends and for public holidays.

## Limits

No bar code reader and no mobile application in this release.

## Future work

Add a bar code reader and send reminder mail two days before the due date.
"""

GROCERY_ITEMS = """# Grocery note

Atta, five kilograms
Toor dal, one kilogram
Groundnut oil, one litre
Sugar, one kilogram
Tea powder, 250 g
Salt
Turmeric and chilli powder
Ghee, 500 g
"""

RECIPE_TEA = """# Masala chai for two cups

## Ingredients
- 1 cup water
- 1 cup milk
- 2 teaspoons loose tea
- 1 small piece of ginger, crushed
- 2 green cardamom pods
- 2 teaspoons sugar

## Method
1. Boil the water with the ginger and the cardamom.
2. Add the tea and boil for one minute.
3. Add the milk and the sugar.
4. Boil three more times, lifting the pan each time.
5. Strain into two cups and serve hot.

Serve with rusk or with a plain biscuit.
"""

INVOICE_FURNITURE = """GAURI FURNITURE HOUSE
Shop 4, Sadashiv Peth, Pune 411030
GSTIN: 27PQRSX6789K1Z2

Tax invoice

Invoice number: 8812
Date: 24 February 2024
Customer: Anil Deshmukh, Flat 704, Gokul Heights, Baner, Pune 411045

| Item | Quantity | Rate | Amount |
| --- | --- | --- | --- |
| Study table, teak | 1 | 8,400 | 8,400 |
| Office chair, mesh back | 2 | 3,250 | 6,500 |
| Bookshelf, five shelves | 1 | 5,900 | 5,900 |
| Delivery and fitting | 1 | 700 | 700 |

Subtotal: 21,500
CGST 9 percent: 1,935
SGST 9 percent: 1,935
Total: 25,370

Warranty: 12 months on the wood work. No warranty on the mesh fabric.
"""

STUB_NOTES = """Lecture notes, first draft

Topic: memory management, part 1.

Points to cover:
- contiguous allocation
- fixed partitions
- variable partitions
- fragmentation, internal and external
- compaction
- paging as the fix

TODO: check the third example with the numbers.
TODO: add the diagram of the page table.
"""

UNTITLED_TXT = """Copy this text into the form.

Reference number: 4471-2288-90
Application: rent free, no, correction: parking card renewal
Tower B, Gokul Heights, Baner Road, Pune 411045

Steps
1. Fill the form at the society office.
2. Attach a copy of the lease paper.
3. Pay 500 rupees and keep the receipt.
"""

CERT_SCAN = """Certificate of participation

This certificate goes to Priya Nair of Gokul Institute of Technology for the participation in the state level coding contest held at Pune on 9 November 2023.

The team solved five of the eight problems and finished in the twelfth place.

Signed: Dr. R. Deshpande, Head of Department
"""

CSV_ROWS = [["id", "item", "amount", "date"]] + [
    [str(i), item, str(amount), date]
    for i, (item, amount, date) in enumerate(
        [
            ("Hostel mess", 3200, "2024-01-05"),
            ("Stationery", 240, "2024-01-07"),
            ("Bus pass", 900, "2024-01-09"),
            ("Lab manual", 180, "2024-01-12"),
            ("Printer ink", 640, "2024-01-14"),
            ("Domain name", 850, "2024-01-19"),
            ("Cloud server", 1420, "2024-01-21"),
            ("Coffee", 310, "2024-01-24"),
            ("Phone cover", 199, "2024-01-28"),
            ("Book, DBMS", 560, "2024-02-02"),
            ("Book, OS", 495, "2024-02-03"),
            ("Movie ticket", 350, "2024-02-09"),
            ("Train fare", 3120, "2024-02-04"),
            ("Snacks", 175, "2024-02-11"),
            ("Electricity share", 890, "2024-02-15"),
            ("Keyboard", 1250, "2024-02-18"),
            ("Internet", 999, "2024-02-20"),
            ("Gift", 750, "2024-02-24"),
        ],
        start=1,
    )
]

# DBMS notes
DBMS_NORM = """# Normalization, lecture notes

Normalization removes redundant data and stops update faults. We start with a large
relation and split it into smaller relations with clean keys.

## Functional dependency

X determines Y when each value of X holds one value of Y. We write X -> Y.

## Normal forms

1. First normal form: every column holds one value. No list inside a cell.
2. Second normal form: first normal form plus no partial dependency on a part of a key.
3. Third normal form: second normal form plus no transitive dependency. A non key column does not determine another non key column.
4. BCNF: for each dependency X -> Y, X is a super key.
5. Fourth normal form: no independent many to many facts in one relation.

## Decomposition

A decomposition is lossless when the join of the parts returns the first relation. A
decomposition keeps dependencies when each first dependency holds inside one part.

## Example

Enrollment(student, course, teacher) with course -> teacher breaks third normal form.
Split it into Enrollment(student, course) and Course(course, teacher).

## Cost

Many joins on a read heavy system cost time. Denormalize on purpose when the report
path needs it, and write down the reason.
"""

DBMS_JOINS = """# SQL joins, lecture notes

A join pairs rows of two relations on a condition.

## Inner join

Returns the rows with a match in both relations.

    select s.name, m.marks
    from student s
    join marks m on m.student_id = s.id;

## Left outer join

Returns all rows of the left relation and the matching rows of the right relation.
Missing right values appear as NULL.

## Right outer join

The same rule with the sides changed.

## Full outer join

Returns the rows of both relations. MySQL has no full outer join. Use a union of a
left join and a right join.

## Cross join

Returns the product of both relations. A cross join with a condition in the where
clause works like an inner join, but the join form reads better.

## Self join

One relation joins to itself. Use it for a hierarchy, for example a manager column.

## Anti join

Rows of the left relation with no match on the right side. Write it with a left join
and a NULL test, or with NOT EXISTS.

## Cost

The query planner picks a nested loop for small inputs and a hash join for large
inputs. Add an index on the join column to help the nested loop path.
"""

DBMS_ACID = """# Transactions and ACID properties, lecture notes

## Transaction

A transaction is one unit of work. Either all its changes stay, or none stay.

## ACID

- Atomicity: the transaction is all or nothing. The log undoes a half done transaction.
- Consistency: a transaction moves the database from one valid state to another valid state.
- Isolation: live transactions do not see the half done work of other transactions.
- Durability: after a commit, the change survives a power failure.

## States

Active, partially committed, committed, failed and aborted.

## Isolation levels

1. Read uncommitted: dirty reads are possible.
2. Read committed: no dirty reads, but non repeatable reads are possible.
3. Repeatable read: no non repeatable reads, but phantom rows are possible.
4. Serializable: full isolation. The lowest speed.

## Problems

- Dirty read: a transaction reads uncommitted data.
- Lost update: two transactions write one row and one change disappears.
- Phantom read: a second read returns new rows.

## Two phase locking

The growing phase takes locks. The shrinking phase releases locks. No lock comes after
the first release.
"""

DBMS_BTREE = """# B+ tree indexing, lecture notes

## Why a B+ tree

A B+ tree holds many keys in one node. The tree stays short and the disk reads stay
few. A table with 10 million rows needs about four levels.

## Shape

- Every inner node holds keys and child pointers.
- Every leaf holds keys and row pointers.
- The leaves form a linked list in key order.
- The tree is balanced. All leaves sit at the same depth.

## Search

Compare the key with the keys in the node. Follow the child pointer. Walk down to a
leaf. A search costs O(log n) node reads.

## Range query

Find the first leaf for the low key. Walk the leaf list to the high key. The linked
list makes a range read cheap.

## Insert

Insert into the leaf. If the leaf is full, split it and push the middle key to the
parent. A split can walk up to the root and add one level.

## Delete

Remove the key. If the node drops below the fill factor, borrow a key from a sibling
or merge with a sibling.

## Clustered and secondary

A clustered index holds the rows in key order. A secondary index holds the key and a
row pointer. Two B+ trees on one table are common.

## Compared with a hash index

A hash index is fast for equality on one key. A B+ tree also answers a range query and
a sorted read.
"""

DBMS_MIXED = """# DBMS revision sheet

This sheet mixes the four big topics of the unit.

## Normalization

A relation in third normal form has no transitive dependency. BCNF is stricter. A
lossless join decomposition returns the first relation after the join.

## SQL

An inner join pairs matching rows. A left outer join keeps all left rows. A cross join
makes the product of both relations.

## Indexing

A B+ tree keeps the keys in order and links the leaves. A range query walks the leaf
list. A hash index answers only equality.

## Transactions

ACID stands for atomicity, consistency, isolation and durability. Two phase locking
holds a growing phase and a shrinking phase. A deadlock stops two transactions that
wait for each other.

## Practice questions

1. Bring the relation R(a, b, c, d) with a -> b and b -> c to third normal form.
2. Write a query that lists each student with the count of marks rows.
3. Name two cases where the planner skips an index.
4. State the difference between a dirty read and a phantom read.
"""

# OS notes
OS_SCHED = """# CPU scheduling, lecture notes

The scheduler picks the next process for the processor.

## Measures

- Turnaround time: finish time minus arrival time.
- Waiting time: turnaround time minus the burst time.
- Response time: first run time minus arrival time.
- Throughput: finished jobs in one second.

## Algorithms

1. First come first served: simple, but one long job blocks the queue.
2. Shortest job first: best average waiting time, needs the burst time in advance.
3. Round robin: a time slice for each process in turn. Good response time.
4. Priority: the highest priority runs first. Starvation is possible.
5. Multilevel feedback queue: several queues with different slices. The process moves
   down when it uses the full slice.

## Formula

Average waiting time = sum of the waiting times / number of processes.

## Preemption

A preemptive scheduler takes the processor away from a live process. A non preemptive
scheduler waits for the process to stop or to block on input.
"""

OS_PAGING = """# Paging and virtual memory, lecture notes

## Paging

The logical address space splits into pages. The physical memory splits into frames of
the same size. A page table maps a page to a frame.

## Address

The logical address holds a page number and an offset. The page number indexes the
page table. The offset stays as it is.

## Fault

A page fault happens when the page sits on the disk. The kernel loads the page into a
free frame and restarts the instruction.

## Replacement

- FIFO: the oldest page leaves. Belady's anomaly is possible.
- LRU: the page with the oldest use leaves. Good, but costly to track.
- Optimal: the page used last in the future leaves. Only a measure, not a real plan.
- Clock: an approximation of LRU with a reference bit.

## Thrashing

The system spends more time on faults than on work. Fix it with a larger frame set or
with a lower degree of multiprogramming.

## Translation lookaside buffer

A small fast cache for page table entries. A hit avoids the memory read of the page
table.
"""

OS_DEADLOCK = """# Deadlocks, lecture notes

## Definition

A deadlock stops a set of processes. Each process waits for a resource that another
process in the set holds.

## Four conditions

1. Mutual exclusion: one process holds a resource at a time.
2. Hold and wait: a process holds one resource and waits for another.
3. No preemption: nobody takes a resource away.
4. Circular wait: a cycle of waits.

All four conditions must hold. Break one condition and no deadlock happens.

## Handling

- Prevention: break a condition. Request all resources at the start, or order the
  resources by number.
- Avoidance: check each request. The banker's algorithm grants a request only when the
  system stays in a safe state.
- Detection: build a wait for graph. A cycle means a deadlock. Find the cycle with a
  depth first search.
- Recovery: kill a process or roll one back. Pick the victim with the lowest cost.

## Banker's algorithm

Track the available vector, the maximum matrix, the allocation matrix and the need
matrix. A state is safe when some order of the processes can finish. That order is the
safe sequence.

## Worked idea

With three resource types A, B, C and five processes, compute need = max - allocation.
Then simulate the requests and find the safe sequence. If no process can finish, the
state is unsafe.
"""

# CN notes
CN_TCP = """# TCP congestion control, lecture notes

TCP adds a congestion window, cwnd, beside the receive window. The sender uses the
smaller of the two windows.

## Slow start

Start with cwnd of one segment. Double cwnd for each round trip. The growth is
exponential.

## Congestion avoidance

After the threshold, add one segment for each round trip. The growth is linear.

## Loss

- Timeout: set the threshold to half of cwnd, drop cwnd to one segment and start slow
  start again.
- Three duplicate ACKs: fast retransmit the lost segment. Set cwnd to half and enter
  fast recovery.

## Tahoe and Reno

Tahoe drops to slow start on any loss. Reno uses fast recovery and keeps cwnd near
half.

## Modern ideas

CUBIC uses a cubic growth curve on a high bandwidth path. BBR measures the bandwidth
and the round trip time instead of a loss.

## Fairness

Many flows on one link drift toward an equal share. A flow with a short round trip
time grows faster and takes more than a fair share.
"""

CN_ROUTING = """# Routing and the network layer, lecture notes

## Tasks

The network layer moves a packet from a source host to a target host across many
networks.

## Distance vector

Each router tells its neighbours its distance to each network. A router updates its
table when a neighbour reports a shorter path. Count to infinity is the known fault.
Split horizon and poisoned reverse limit it.

## Link state

Each router floods the state of its links. Every router builds the full graph and runs
Dijkstra's algorithm. OSPF works this way.

## Path vector

Each router advertises the full path. BGP uses this plan between the autonomous
systems.

## Interior and exterior

- Interior gateway protocols: RIP, OSPF, IS-IS.
- Exterior gateway protocol: BGP.

## Tables

The forwarding table holds the next hop for a prefix. The longest prefix match wins.

## Header fields

Version, header length, total length, time to live, protocol, source address and
target address. The time to live stops a packet that loops for ever.
"""

CN_SUBNET = """# Subnetting and IP addressing, lecture notes

## IPv4 address

32 bits, written as four decimal numbers. The address holds a network part and a host
part.

## Classless addressing

A prefix length marks the network bits. 192.168.10.0/24 holds 24 network bits.

## Mask

/24 gives the mask 255.255.255.0. The number of hosts is 2^(32-prefix) - 2.

## Steps

1. Write the prefix length.
2. Find the block size as 2^(32 - prefix).
3. List the subnet addresses in steps of the block size.
4. Find the broadcast address as the last address before the next subnet.
5. Assign the host range between the network address and the broadcast address.

## Example

Split 192.168.10.0/24 into four subnets. Take /26. Each subnet holds 62 hosts.

- 192.168.10.0/26, hosts 1 to 62, broadcast 63
- 192.168.10.64/26, hosts 65 to 126, broadcast 127
- 192.168.10.128/26, hosts 129 to 190, broadcast 191
- 192.168.10.192/26, hosts 193 to 254, broadcast 255

## VLSM

Variable length subnet masking gives each link the size that it needs. A point to point
link takes /30 and wastes only two addresses.

## Private ranges

10.0.0.0/8, 172.16.0.0/12 and 192.168.0.0/16 stay inside the local network. NAT maps
them to one public address.
"""


# ---------------------------------------------------------------- builders
def build_downloads(dl: Path) -> None:
    """Build the Downloads folder with about 30 messy files."""
    lease_text()
    make_pdf(dl / "scan_0042.pdf", [LEASE_PAGE_1, LEASE_PAGE_2])

    make_docx(dl / "Untitled.docx", INVOICE_WEB)

    make_pdf(dl / "Untitled(1).pdf", [CV_TEXT])

    write_text(dl / "IMG_2033.md", GOA_TRIP)
    write_text(dl / "notes_final_v2.md", PAV_BHAJI)

    make_pdf(dl / "statement(2).pdf", [BANK_STATEMENT])

    make_pdf(dl / "doc_apr.pdf", [PHONE_BILL])
    make_pdf(dl / "hw1.pdf", [HW_DBMS])
    make_pdf(dl / "scan_0043.pdf", [ELECTRICITY_BILL])
    make_pdf(dl / "IMG_2044.pdf", [TRAIN_TICKET])
    make_pdf(dl / "download (7).pdf", [ORDER_CONFIRM])

    write_text(dl / "todo.md", SHOPPING_LIST)
    write_text(dl / "meeting_notes_2024-02-14.txt", MEETING_NOTES)
    write_text(dl / "wifi_password.txt", WIFI_NOTES)
    write_text(dl / "Untitled.txt", UNTITLED_TXT)
    write_text(dl / "lecture_notes_stub.md", STUB_NOTES)
    write_text(dl / "grocery_list.md", GROCERY_ITEMS)
    write_text(dl / "recipe_notes.md", RECIPE_TEA)

    write_csv(dl / "data_backup.csv", CSV_ROWS)

    make_pdf(dl / "syllabus_sem5.pdf", [SYLLABUS])
    make_pdf(dl / "hall_ticket.pdf", [HALL_TICKET])
    make_pdf(dl / "document(3).pdf", [CERT_SCAN])
    make_pdf(dl / "invoice_8812.pdf", [INVOICE_FURNITURE])

    make_docx(dl / "cover_letter.docx", COVER_LETTER)
    make_docx(dl / "project_report_final(FINAL).docx", PROJECT_REPORT)

    rnd = random.Random(4242)
    make_image(dl / "IMG_2031.png", "screenshot", rnd)
    make_image(dl / "IMG_2032.jpg", "lines", rnd)
    make_image(dl / "screenshot_2024-03-11.png", "flow", rnd)
    make_image(dl / "IMG_2035.jpg", "chart", rnd)

    write_bytes(dl / "setup-installer.AppImage", os.urandom(3_200_000))
    write_bytes(dl / "cache_dump.bin", os.urandom(2_100_000))


def build_documents(doc: Path) -> None:
    """Build the semester 5 study folders."""
    dbms = doc / "Sem5" / "DBMS"
    os_dir = doc / "Sem5" / "OS"
    cn = doc / "Sem5" / "CN"

    write_text(dbms / "dbms_unit1.md", DBMS_NORM)
    make_big_pdf(dbms / "dbms_unit2.pdf", [DBMS_JOINS])
    make_pdf(dbms / "dbms_unit3.pdf", [DBMS_BTREE])
    make_docx(dbms / "dbms_unit4.docx", DBMS_ACID)
    make_pdf(dbms / "dbms_unit5.pdf", [DBMS_MIXED])

    make_pdf(os_dir / "os_unit3.pdf", [OS_SCHED])
    write_text(os_dir / "os_unit2.md", OS_PAGING)
    make_pdf(os_dir / "os_unit4.pdf", [OS_DEADLOCK])

    make_big_pdf(cn / "cn_unit4.pdf", [CN_TCP])
    write_text(cn / "cn_unit5.md", CN_ROUTING)
    make_pdf(cn / "cn_unit6.pdf", [CN_SUBNET])


def build_pictures(pic: Path) -> None:
    """Build ten images. Two of them are exact copies of one file."""
    rnd = random.Random(777)
    base = make_image(pic / "IMG_8801.jpg", "sunset", rnd)
    shutil.copyfile(base, pic / "sunset_photo.jpg")
    stamp_mtime(pic / "sunset_photo.jpg")

    make_image(pic / "IMG_8802.png", "circles", rnd)
    make_image(pic / "diagram_flow.png", "flow", rnd)
    make_image(pic / "chart_sales.png", "chart", rnd)
    make_image(pic / "checkerboard_tile.png", "checker", rnd)
    make_image(pic / "avatar_circle.png", "avatar", rnd)
    make_image(pic / "signup_screenshot.png", "screenshot", rnd)
    make_image(pic / "sketch_lines.jpg", "lines", rnd)
    make_image(pic / "tiles_pattern.png", "tiles", rnd)
    make_text_screenshot(pic / "Screenshot_20240402_upi.png", UPI_LINES)


UPI_LINES = [
    "Payment successful",
    "Paid to Gokul Heights Society",
    "Amount: Rs 3,500.00",
    "UPI transaction ID 409312784455",
    "Maintenance charges for April 2024",
]


def make_text_screenshot(path: Path, lines: list[str]) -> None:
    """A phone screenshot with text, so OCR has something to read (phase 8)."""
    from PIL import ImageFont

    img = Image.new("RGB", (720, 520), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    d.rectangle([0, 0, 720, 70], fill=(30, 110, 60))
    for i, line in enumerate(lines):
        d.text((40, 110 + i * 70), line, fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    stamp_mtime(path)


def build_videos(vid: Path, pic: Path) -> None:
    """A slideshow video of four test pictures, 4 s each (search by appearance, 13.12 item 5).

    "a bar chart" must find this video at 4 s, and "a checkerboard" at 8 s. Needs ffmpeg.
    """
    if shutil.which("ffmpeg") is None:
        print("ffmpeg is not installed: no test video")
        return
    vid.mkdir(parents=True, exist_ok=True)
    frames = ["sunset_photo.jpg", "chart_sales.png", "checkerboard_tile.png", "diagram_flow.png"]
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    for f in frames:
        args += ["-loop", "1", "-t", "4", "-i", str(pic / f)]
    chain = "".join(f"[{i}:v]scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25[v{i}];" for i in range(len(frames)))
    chain += "".join(f"[v{i}]" for i in range(len(frames))) + f"concat=n={len(frames)}:v=1:a=0[out]"
    # A keyframe at each slide, as a phone camera makes at a scene change.
    audio = []
    with tempfile.TemporaryDirectory(prefix="raven-tts-") as tmp:
        # Speech for the transcript test (13.12): espeak-ng reads a short project update.
        if shutil.which("espeak-ng"):
            wav = Path(tmp) / "talk.wav"
            subprocess.run(["espeak-ng", "-s", "150", "-w", str(wav), VIDEO_TALK], check=True)
            args += ["-i", str(wav)]
            audio = ["-map", f"{len(frames)}:a", "-c:a", "aac"]
            memo = vid.parent / "Recordings"
            memo.mkdir(parents=True, exist_ok=True)
            subprocess.run(["espeak-ng", "-s", "150", "-w", str(Path(tmp) / "memo.wav"), MEMO_TALK], check=True)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(Path(tmp) / "memo.wav"), "-c:a", "aac",
                            str(memo / "voice_memo_0412.m4a")], check=True)
            stamp_mtime(memo / "voice_memo_0412.m4a")
        args += ["-filter_complex", chain, "-map", "[out]", *audio, "-c:v", "libx264", "-g", "100", "-pix_fmt", "yuv420p",
                 str(vid / "VID_20250306_slides.mp4")]
        subprocess.run(args, check=True)
    stamp_mtime(vid / "VID_20250306_slides.mp4")


VIDEO_TALK = ("This is the project review for the drone club. The budget for the new frame is forty two thousand rupees. "
              "The test flight is on the ninth of March at the college ground.")
MEMO_TALK = ("Reminder for myself. The library book on compilers is due on the twentieth. "
             "Pay the hostel mess fee of three thousand two hundred rupees before Friday.")


def build_project(proj: Path) -> None:
    """Build a small python project from the third semester."""
    proj.mkdir(parents=True, exist_ok=True)

    write_text(
        proj / "main.py",
        '''"""Entry point for the grade book tool."""

from grades import average, letter_grade


def main() -> None:
    marks = [72, 65, 88, 41, 90]
    print("average:", round(average(marks), 2))
    print("grade:", letter_grade(average(marks)))


if __name__ == "__main__":
    main()
''',
    )

    write_text(
        proj / "grades.py",
        '''"""Small helpers for the grade book."""


def average(marks):
    if not marks:
        raise ValueError("empty mark list")
    return sum(marks) / len(marks)


def letter_grade(score):
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    return "D"
''',
    )

    write_text(
        proj / "utils.py",
        '''"""File helpers for the old project."""

import csv


def read_marks(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    return rows


def total(rows):
    return sum(int(row["marks"]) for row in rows)
''',
    )

    write_text(
        proj / "README.md",
        """# Grade book tool

A small semester 3 project. The tool reads a CSV file of student marks and prints
the average and the letter grade.

Run it:

    python main.py

Known faults: the CSV reader assumes the column name `marks`.
""",
    )

    write_text(
        proj / "TODO.md",
        """# Work left

- add a test file
- handle a missing CSV file
- move the hard coded name list to the CSV
- fix the letter grade limits for the D grade
""",
    )

    write_text(
        proj / "run.log",
        """2023-11-02 10:14:02 INFO start grade book tool
2023-11-02 10:14:02 INFO read students.csv, 5 rows
2023-11-02 10:14:02 INFO average 71.2, grade B
2023-11-05 17:40:11 ERROR students.csv not found
2023-11-05 17:41:03 INFO retry with the sample file
2023-11-05 17:41:03 INFO average 71.2, grade B
""",
    )

    write_csv(
        proj / "students.csv",
        [["roll", "name", "marks"]]
        + [
            [f"23{i:03d}", name, str(marks)]
            for i, (name, marks) in enumerate(
                [
                    ("Asha Patil", 72),
                    ("Rohit Shetty", 65),
                    ("Meera Iyer", 88),
                    ("Zaid Khan", 41),
                    ("Tara Bose", 90),
                ],
                start=1,
            )
        ],
    )


def build_desktop(desk: Path) -> None:
    """Build a few desktop files plus one essay."""
    write_text(
        desk / "quick_notes.txt",
        """Quick notes

- Call the landlord about the water motor on Saturday.
- Renew the library books before 20 March.
- Buy a birthday gift for Meera.
- Print the hall ticket at the shop near the gate.
""",
    )
    write_text(
        desk / "passwords.txt",
        """Old logins, move these to a safe place.

college portal: priya.nair / portal-4417
library account: 5C118 / library-card-88
practice site: priya@example.com / cloudy-panda-12
""",
    )
    write_text(
        desk / "society_notice.md",
        """# Society notice

The water tank cleaning happens on 24 March from 09:00 to 14:00. No water in this
window. Please store water in advance.

The parking list is closed for new entries until the annual general meeting.

Regards, Secretary, Gokul Heights Co-operative Housing Society.
""",
    )
    write_csv(
        desk / "expenses.csv",
        [["month", "item", "amount"]]
        + [
            ["January", "books", "1450"],
            ["February", "travel", "3120"],
            ["March", "electricity", "2656"],
        ],
    )
    make_docx(
        desk / "essay_on_clean_cities.docx",
        """# Why clean cities matter

A clean city protects the health of the people who live in it. Waste that stays on
the street blocks the drains and brings disease in the monsoon. Clean public places
also bring a feeling of safety and pride to a neighbourhood.

The first step is a working collection system. Bins on every street help, but a
regular truck route matters more. The second step is the separation of waste at the
home. Wet waste becomes compost and dry waste goes to a recycling plant.

Citizens hold a large part of the duty. A person who drops a wrapper on the road
costs the city money and time. Schools can teach the habit early.

A clean city is not only a pretty city. It is a city with less illness, fewer
blocked drains and a lower bill for the public health system. The work is small and
daily, and the result is large.
""",
    )


# ---------------------------------------------------------------- queries
QUERIES = [
    {"query": "rent agreement", "expected": "Downloads/scan_0042.pdf"},
    {"query": "invoice for the website design work", "expected": "Downloads/Untitled.docx"},
    {"query": "curriculum vitae for a python and django backend developer",
     "expected": "Downloads/Untitled(1).pdf"},
    {"query": "seven day travel plan for a beach holiday in Goa with flight times",
     "expected": "Downloads/IMG_2033.md"},
    {"query": "recipe for pav bhaji", "expected": "Downloads/notes_final_v2.md"},
    {"query": "savings account statement with PAN and Aadhaar numbers",
     "expected": "Downloads/statement(2).pdf"},
    {"query": "postpaid mobile bill with data usage and call charges",
     "expected": "Downloads/doc_apr.pdf"},
    {"query": "home work questions about database normalization and normal forms",
     "expected": "Downloads/hw1.pdf"},
    {"query": "electricity bill for a flat in Pune with a meter reading",
     "expected": "Downloads/scan_0043.pdf"},
    {"query": "railway journey ticket from Pune to Delhi with a berth number",
     "expected": "Downloads/IMG_2044.pdf"},
    {"query": "online order confirmation for wireless headphones",
     "expected": "Downloads/download (7).pdf"},
    {"query": "shopping list with milk, rice and vegetables",
     "expected": "Downloads/todo.md"},
    {"query": "notes on TCP slow start and the congestion window",
     "expected": "Documents/Sem5/CN/cn_unit4.pdf"},
    {"query": "notes on B+ tree indexing and range queries",
     "expected": "Documents/Sem5/DBMS/dbms_unit3.pdf"},
    {"query": "notes on deadlocks and the banker's algorithm",
     "expected": "Documents/Sem5/OS/os_unit4.pdf"},
]


# ---------------------------------------------------------------- main

def _age(path: Path, days: int) -> None:
    """Set both access and modified times `days` ago, so the file looks cold."""
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def build_junk(out: Path) -> None:
    """Junk for the Space cleanup test (PLAN.md 9.7): rebuildable folders, partial files, an empty folder."""
    web = out / "Projects" / "web-app"
    write_text(web / "package.json", '{"name": "web-app", "version": "1.0.0", "dependencies": {"left-pad": "1.3.0"}}\n')
    write_text(web / "index.js", "const pad = require('left-pad')\nconsole.log(pad('raven', 10))\n")
    for i in range(40):
        write_text(web / "node_modules" / f"pkg{i}" / "index.js", ("module.exports = () => 'x'\n" * 400))
        write_text(web / "node_modules" / f"pkg{i}" / "package.json", f'{{"name": "pkg{i}"}}\n')
    for p in [web / "package.json", web / "index.js", web, *web.rglob("*")]:
        _age(p, 120)
    cache = out / "Projects" / "old-sem3" / "__pycache__"
    write_bytes(cache / "grades.cpython-312.pyc", os.urandom(6000))
    _age(cache / "grades.cpython-312.pyc", 200)
    _age(cache, 200)
    part = write_bytes(out / "Downloads" / "lecture_recording.mp4.part", os.urandom(300_000))
    _age(part, 40)
    empty = out / "Downloads" / "New Folder"
    empty.mkdir(parents=True, exist_ok=True)
    _age(empty, 60)


def build_cold(out: Path) -> None:
    """Cold files for the compression test (PLAN.md 9.8): old logs and CSV, and old JPEG photos."""
    logs = out / "Projects" / "old-sem2-lab"
    rnd = random.Random(7)
    for i in range(6):
        lines = [
            f"2025-0{1 + i % 8}-{10 + i:02d} 10:{m:02d}:{rnd.randint(0, 59):02d} INFO sensor={rnd.randint(1, 9)} reading={rnd.random():.4f} status=ok"
            for m in range(60)
            for _ in range(120)
        ]
        write_text(logs / f"run_{i + 1}.log", "\n".join(lines) + "\n")
    rows = [["id", "roll", "subject", "marks"]] + [[str(i), f"21CE{1000 + i}", rnd.choice(["DBMS", "OS", "CN"]), str(rnd.randint(20, 100))] for i in range(30000)]
    write_csv(logs / "marks_2025.csv", rows)
    write_text(logs / "README.md", "# Lab readings, semester 2\n\nRaw sensor logs from the physics lab.\n")
    for p in [*logs.rglob("*"), logs]:
        _age(p, 210)
    trip = out / "Pictures" / "2025-trip"
    for i in range(4):
        img = Image.new("RGB", (1600, 1200))
        px = img.load()
        for y in range(0, 1200, 4):
            for x in range(0, 1600, 4):
                c = ((x * 7 + i * 40) % 256, (y * 3 + i * 25) % 256, ((x + y) // 5) % 256)
                for dy in range(4):
                    for dx in range(4):
                        px[x + dx, y + dy] = c
        dst = trip / f"DSC_{4100 + i}.jpg"
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(dst, "JPEG", quality=90)
        _age(dst, 300)
    _age(trip, 300)


def main(argv: list[str]) -> int:
    out = Path(argv[1]).expanduser() if len(argv) > 1 else SCRIPT_DIR / "home"
    out = out.resolve()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    try:
        for folder in (
            "Downloads",
            "Documents/Sem5/DBMS",
            "Documents/Sem5/OS",
            "Documents/Sem5/CN",
            "Pictures",
            "Projects/old-sem3",
            "Desktop",
            "Submissions",
        ):
            (out / folder).mkdir(parents=True, exist_ok=True)

        build_downloads(out / "Downloads")
        build_documents(out / "Documents")
        build_pictures(out / "Pictures")
        build_videos(out / "Videos", out / "Pictures")
        build_project(out / "Projects" / "old-sem3")
        build_desktop(out / "Desktop")
        build_junk(out)
        build_cold(out)

        queries_path = SCRIPT_DIR / "queries.json"
        queries_path.write_text(
            json.dumps(QUERIES, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    finally:
        if _scratch_root is not None:
            shutil.rmtree(_scratch_root, ignore_errors=True)

    files = [p for p in out.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"files: {len(files)}")
    print(f"bytes: {total}")
    print(f"output: {out}")
    print(f"queries: {SCRIPT_DIR / 'queries.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
