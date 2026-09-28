"""Links that open a booking site's own search page for a hotel and dates (their live prices).

These are plain public search URLs: the guest's browser loads the site normally. We don't fetch or
scrape those pages; the sites' terms forbid scraping and require partner APIs for data access.
"""

from urllib.parse import urlencode

from app.shopper.contracts import StaySearch


def check_links(hotel_name: str, q: StaySearch) -> list[dict]:
    place = f"{hotel_name}, {q.city}"
    booking = {"ss": place, "checkin": q.check_in.isoformat(), "checkout": q.check_out.isoformat(),
               "group_adults": q.adults, "no_rooms": 1, "group_children": len(q.children_ages)}
    booking_url = "https://www.booking.com/searchresults.html?" + urlencode(booking) + "".join(
        f"&age={a}" for a in q.children_ages)
    expedia = {"destination": place, "startDate": q.check_in.isoformat(), "endDate": q.check_out.isoformat(),
               "adults": q.adults}
    if q.children_ages:
        expedia["children"] = ",".join(f"1_{a}" for a in q.children_ages)
    google = {"q": f"{place} hotel {q.check_in.isoformat()} to {q.check_out.isoformat()}"}
    return [
        {"label": "Booking.com", "url": booking_url},
        {"label": "Expedia", "url": "https://www.expedia.com/Hotel-Search?" + urlencode(expedia)},
        {"label": "Google Hotels", "url": "https://www.google.com/travel/search?" + urlencode(google)},
    ]


def site_search_link(source: str, hotel_name: str, q: StaySearch) -> str | None:
    """The site's own search page for this hotel and dates, for sources we know how to link."""
    links = {link["label"].casefold(): link["url"] for link in check_links(hotel_name, q)}
    key = source.casefold()
    if "booking.com" in key:
        return links["booking.com"]
    if "expedia" in key:
        return links["expedia"]
    return None
