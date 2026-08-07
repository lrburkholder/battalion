# Test cases for Battalion based on spec.md
# These tests should fail until the features are implemented

import pytest
from battalion import Battalion

def test_battalion_initialization():
    """Test that a Battalion can be initialized with valid parameters."""
    battalion = Battalion(name="Alpha", size=100)
    assert battalion.name == "Alpha"
    assert battalion.size == 100

def test_battalion_invalid_size():
    """Test that a Battalion raises an error for invalid size."""
    with pytest.raises(ValueError):
        Battalion(name="Beta", size=-10)

def test_battalion_add_soldier():
    """Test adding a soldier to the battalion."""
    battalion = Battalion(name="Charlie", size=50)
    battalion.add_soldier("John Doe")
    assert "John Doe" in battalion.soldiers

def test_battalion_remove_soldier():
    """Test removing a soldier from the battalion."""
    battalion = Battalion(name="Delta", size=50)
    battalion.add_soldier("Jane Doe")
    battalion.remove_soldier("Jane Doe")
    assert "Jane Doe" not in battalion.soldiers

def test_battalion_deploy():
    """Test deploying the battalion."""
    battalion = Battalion(name="Echo", size=100)
    assert not battalion.is_deployed
    battalion.deploy()
    assert battalion.is_deployed

def test_battalion_undeploy():
    """Test undeploying the battalion."""
    battalion = Battalion(name="Foxtrot", size=100)
    battalion.deploy()
    assert battalion.is_deployed
    battalion.undeploy()
    assert not battalion.is_deployed

def test_battalion_status():
    """Test getting the status of the battalion."""
    battalion = Battalion(name="Golf", size=75)
    status = battalion.status()
    assert isinstance(status, dict)
    assert "name" in status
    assert "size" in status
    assert "soldiers" in status
    assert "is_deployed" in status