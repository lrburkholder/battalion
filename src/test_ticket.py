# Test cases for the ticket feature

import unittest
from battalion import Ticket

class TestTicket(unittest.TestCase):
    def test_ticket_creation(self):
        """Test that a ticket can be created with valid parameters."""
        ticket = Ticket(title="Test Ticket", description="Test Description", priority=1)
        self.assertEqual(ticket.title, "Test Ticket")
        self.assertEqual(ticket.description, "Test Description")
        self.assertEqual(ticket.priority, 1)

    def test_ticket_priority_validation(self):
        """Test that ticket priority must be between 1 and 5."""
        with self.assertRaises(ValueError):
            Ticket(title="Invalid Priority", description="Test", priority=0)
        with self.assertRaises(ValueError):
            Ticket(title="Invalid Priority", description="Test", priority=6)

    def test_ticket_status_update(self):
        """Test that ticket status can be updated."""
        ticket = Ticket(title="Status Test", description="Test", priority=3)
        ticket.update_status("In Progress")
        self.assertEqual(ticket.status, "In Progress")

if __name__ == '__main__':
    unittest.main()