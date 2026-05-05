# ofxstatement-consorsbank - Consorsbank PDF statement plugin for ofxstatement
# Copyright (C) 2026  Eduard Ralph
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


from importlib.metadata import PackageNotFoundError, version as _pkg_version


def plugin_version() -> str:
    """Installed package version, or ``"unknown"`` when unavailable.

    Logged as the first INFO line of each parser's ``parse()`` so a user
    reading the convert output can confirm which install actually ran,
    without having to drop out and ``pip show ofxstatement-consorsbank``.
    Useful when multiple checkouts or a mix of pip / pipx / system
    installs are in play.

    Resolved at runtime via ``importlib.metadata`` so editable installs
    pick up whatever the last reinstall pinned. Falls back to
    ``"unknown"`` when the distribution metadata is absent (running
    tests directly out of a source tree without ``pip install -e``).
    """
    try:
        return _pkg_version("ofxstatement-consorsbank")
    except PackageNotFoundError:
        return "unknown"
