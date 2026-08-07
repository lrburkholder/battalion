def test_ticket_creation():
    from battalion.ticket import create_ticket
    ticket = create_ticket(title='Test', description='Test description')
    assert ticket['title'] == 'Test'
    assert ticket['description'] == 'Test description'
    assert 'id' in ticket

def test_ticket_retrieval():
    from battalion.ticket import get_ticket
    ticket = get_ticket(id=1)
    assert ticket is not None
    assert 'id' in ticket