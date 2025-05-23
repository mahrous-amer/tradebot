class Observer:
    """Observer base class.

    This class should be inherited by any class that wishes to receive updates
    from an observable. Subclasses must implement the `update` method.
    """

    def update(self):
        """Gets invoked when the observable has a change in state.

        This method must be implemented in any subclass.

        :raises NotImplementedError: To ensure that it gets implemented by any subclass.
        """
        raise NotImplementedError


class Observable:
    """Observable base class.

    This class allows objects (observers) to register themselves to be notified
    of changes in the observable's state. It manages a list of observers and
    provides methods to add, delete, and notify observers.
    """

    def __init__(self):
        """Initializes an Observable object.

        The constructor initializes an empty list of observers and a flag `changed`
        which indicates if the observable has undergone a state change.
        """
        self.observers = []
        self.changed = False

    def add_observer(self, observer):
        """Registers an observer to be notified of state changes.

        :param observer: The observer object to add to the observer list.
        :type observer: Observer
        """
        if observer not in self.observers:
            self.observers.append(observer)

    def delete_observer(self, observer):
        """Removes an observer from the notification list.

        :param observer: The observer object to remove from the observer list.
        :type observer: Observer
        """
        self.observers.remove(observer)

    async def notify_observers(self):
        """Notify all observers when the state has changed.

        This method checks the `changed` flag, and if it is set to `True`, it
        invokes the `update()` method on all observers. After notifying the
        observers, the `changed` flag is reset.

        :return: None
        """
        if not self.changed:
            return

        self.clear_changed()

        for observer in self.observers:
            await observer.update()

    def delete_observers(self):
        """Removes all observers from the notification list.

        :return: None
        """
        self.observers = []

    def set_changed(self):
        """Marks the observable as changed.

        This method sets the `changed` flag to `True`, indicating that the
        observable's state has changed and observers should be notified.

        :return: None
        """
        self.changed = True

    def clear_changed(self):
        """Resets the `changed` flag.

        This method clears the `changed` flag, indicating that the state
        has not changed and observers do not need to be notified.

        :return: None
        """
        self.changed = False

    def has_changed(self):
        """Checks if the observable has changed.

        :return: True if the observable's state has changed, False otherwise.
        :rtype: bool
        """
        return self.changed

    def count_observers(self):
        """Counts the number of registered observers.

        :return: The number of observers currently registered.
        :rtype: int
        """
        return len(self.observers)

