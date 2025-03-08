import uuid

affiliate_data = {}  # In-memory storage for affiliate data (affiliate_id: data)

def generate_affiliate_id():
    """Generates a unique affiliate ID."""
    return str(uuid.uuid4())

def store_affiliate_data(affiliate_id, data):
    """Stores affiliate data."""
    affiliate_data[affiliate_id] = data

def get_affiliate_data(affiliate_id):
    """Retrieves affiliate data."""
    return affiliate_data.get(affiliate_id)