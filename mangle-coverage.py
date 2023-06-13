#!/usr/bin/python

# pylint: disable-msg=invalid-name
# pylint: disable-msg=redefined-outer-name

from collections import namedtuple
from pathlib import Path
import os
import re
import subprocess
import sys

FnRecord = namedtuple("FnRecord", ["name", "start", "end"])

def is_fnda_line_for_fn(line, fn):
    """ Is this a FNDA line describing a given function? """

    return line.startswith("FNDA:") and line.endswith(",%s" % fn)

def is_line_in_fn(line, start, end):
    """ If this is a DA line, does the line number fall within the range
        [start, end]?
    """

    if not line.startswith("DA:"):
        return False

    line = line.removeprefix("DA:")
    (line_no, _) = line.split(",")

    return int(line_no) >= start and (end is None or int(line_no) <= end)

def is_static_fn(lst, fn):
    """ Is the given function static? """

    return fn in lst

def tested_fns():
    """ Return a list of all functions which have a unit test.  Luckily, we
        give the test files a name that matches the function.
    """

    fns = []

    p = Path(".")
    test_files = list(p.glob("**/*_test.c"))

    for f in test_files:
        fns.append(f.name.removesuffix("_test.c"))

    return sorted(fns)

def fns_in_record(record):
    """ Given a record, return a list of (function, first line, last line) tuples.
        If the function is the last in the record, last line will be None.
    """

    tuples = []

    # First pass - generate a list where all the last lines are set to None.
    for line in record:
        if not line.startswith("FN:"):
            continue

        line = line.removeprefix("FN:")
        (line_no, fn) = line.split(",")

        tuples.append(FnRecord(fn, int(line_no), None))

    # Second pass - iterate over the tuples list and fix up the last lines.
    for i in range(0, len(tuples) - 1):
        tuples[i] = tuples[i]._replace(end=tuples[i+1].start - 1)

    return tuples

def fn_executed(record, fn):
    """ Given a record and a function name, return whether that function was
        actually executed.
    """

    for line in record:
        if not is_fnda_line_for_fn(line, fn):
            continue

        line = line.removeprefix("FNDA:")
        (cnt, _) = line.split(",")

        return cnt != "0"

    return False

def recordize_info_file():
    """ Split an lcov output file into a list of records.  A record is simply
        a list of lines, nothing fancy.
    """

    records = []

    with open(sys.argv[1]) as lcov:
        this_record = []

        for line in lcov:
            line = line.strip()

            if line == "end_of_record":
                records.append(this_record)
                this_record = []
                continue

            this_record.append(line)

    return records

def static_fns():
    """ Return a list of all static functions """

    # This is lame, lame, lame but I don't feel like writing the equivalent
    # in python.

    fns = []

    # grep all the source files for lines that begin with "static".
    # Note that we only care about static functions in the lib directory
    # for now.  Static functions in include/ are not really static in
    # the same sense and can have unit tests written.

    try:
        output = subprocess.check_output("find lib -name '*.[ch]' | xargs grep -h -A 1 ^static",
                                         shell=True)
    except subprocess.CalledProcessError:
        return fns

    for line in output.decode().split("\n"):
        # If the line doesn't contain an opening paren, it's definitely not
        # a function declaration.
        if "(" not in line:
            continue

        # If the line contains an equals, it's probably a variable declaration.
        if "=" in line:
            continue

        # Drop everything from the opening paren to the end of line.  This gets
        # rid of all the function parameters.
        line = line.split("(")[0]

        # Having done that, we can now drop everything up through the rightmost
        # space.  This catches the couple cases where we have the "static void"
        # or whatever type on the same line as the function name.
        line = line.split(" ")[-1]

        # Strip out anything that's not valid in a C identifier.
        line = re.sub(r'[\W]', '', line)

        if not line:
            continue

        fns.append(line)

    return fns

def erase_function_from_record(record, fr):
    """ Remove a function from the given coverage record """

    new_lines = []
    executed_lines = 0

    for line in record:
        # Reset the execution count for the function to 0.
        if is_fnda_line_for_fn(line, fr.name):
            new_lines.append("FNDA:0,%s" % fr.name)

        # Remove the function from the total number of functions hit.
        elif line.startswith("FNH:"):
            (_, cnt) = line.split(":")
            new_lines.append("FNH:%d" % (int(cnt) - 1))

        # Reset the exection count for each line in the function to 0.
        elif is_line_in_fn(line, fr.start, fr.end):
            line = line.removeprefix("DA:")
            (line_no, cnt) = line.split(",")
            new_lines.append("DA:%s,0" % line_no)

            if cnt != "0":
                executed_lines += 1

        # Remove the count of the function's executed lines from the
        # total.
        elif line.startswith("LH:"):
            (_, cnt) = line.split(":")
            new_lines.append("LH:%d" % (int(cnt) - executed_lines))

        else:
            new_lines.append(line)

    return new_lines

def render_record(record):
    """ Convert a record into a string that can be printed out """

    return "\n".join(record + ["end_of_record"])

if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        print("usage: %s <coverage_file.info>" % sys.argv[0])
        sys.exit()

    tested = tested_fns()
    static = static_fns()
    records = recordize_info_file()

    for r in records:
        for fr in fns_in_record(r):
            # If the function wasn't executed, there's nothing to do.
            if not fn_executed(r, fr.name):
                continue

            # If the function is static, we won't be writing a unit test for it
            # so leave its coverage alone.  Chances are, some public function
            # wraps it and tests it well enough.
            if is_static_fn(static, fr.name):
                continue

            # The executed public function is not in the list of functions that
            # we have a unit test for.  Remove its coverage data.
            if fr.name not in tested:
                r = erase_function_from_record(r, fr)

        print(render_record(r))
