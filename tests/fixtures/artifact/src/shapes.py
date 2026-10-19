"""Shapes and their areas."""

import math


class Circle:
    def __init__(self, radius):
        self.radius = radius

    def area(self):
        return math.pi * self.radius**2

    def scaled(self, factor):
        def grow(value):
            return value * factor

        return Circle(grow(self.radius))


def ratio(a, b):
    return a / b
