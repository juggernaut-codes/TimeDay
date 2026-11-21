import gc
import unittest
import weakref

from gi.repository import GObject

from timeday.widgets import weak_callback


class CallbackOwner(GObject.Object):
    def accept(self, value):
        self.value = value


class WidgetCallbackTests(unittest.TestCase):
    def test_signal_callback_does_not_keep_its_widget_owner_alive(self):
        owner = CallbackOwner()
        callback = weak_callback(owner.accept)
        callback("saved")
        self.assertEqual(owner.value, "saved")
        reference = weakref.ref(owner)
        del owner
        gc.collect()
        self.assertIsNone(reference())
        self.assertIsNone(callback("late signal"))

    def test_plain_callbacks_retain_their_arguments_and_result(self):
        def add(left, right):
            return left + right

        self.assertEqual(weak_callback(add)(2, right=3), 5)

    def test_native_gobject_callback_accepts_an_explicit_weak_owner(self):
        owner = CallbackOwner()
        callback = weak_callback(owner.notify, owner)
        reference = weakref.ref(owner)
        del owner
        gc.collect()
        self.assertIsNone(reference())
        self.assertIsNone(callback("late-notification"))
