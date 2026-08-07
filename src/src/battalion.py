class Battalion:
    def __init__(self, name, size):
        if size < 0:
            raise ValueError("Size must be non-negative")
        self.name = name
        self.size = size
        self.soldiers = []
        self.is_deployed = False

    def add_soldier(self, soldier_name):
        self.soldiers.append(soldier_name)

    def remove_soldier(self, soldier_name):
        if soldier_name in self.soldiers:
            self.soldiers.remove(soldier_name)

    def deploy(self):
        self.is_deployed = True

    def undeploy(self):
        self.is_deployed = False

    def status(self):
        return {
            "name": self.name,
            "size": self.size,
            "soldiers": self.soldiers,
            "is_deployed": self.is_deployed
        }


class Ticket:
    def __init__(self, title, description, priority):
        if priority < 1 or priority > 5:
            raise ValueError("Priority must be between 1 and 5")
        self.title = title
        self.description = description
        self.priority = priority
        self.status = "Open"

    def update_status(self, new_status):
        self.status = new_status