#!/usr/bin/python

# pylint: disable-msg=invalid-name
# pylint: disable-msg=redefined-outer-name

from collections import namedtuple
from pathlib import Path
import networkx as nx
import os
import re
import subprocess
import sys

FnRecord = namedtuple("FnRecord", ["name", "start", "end"])

def is_fnda_line_for_fn(line, fn):
    """ Is this a FNDA line describing a given function? """

    return line.startswith("FNDA:") and line.endswith(f",{fn}")

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

def private_fn_with_tested_public(lst, fn):
    """ Is this a private function (one that starts with "pcmk__") and if so,
        is there a public version (one that starts with "pcmk_") in the list
        of tested functions?
    """

    return fn.startswith("pcmk__") and fn.replace("pcmk__", "pcmk_", 1) in lst

def source_file(record):
    """ Return the source file described by a given record """

    for line in record:
        if not line.startswith("SF:"):
            continue

        return line.removeprefix("SF:")

    return None

def tested_fns():
    """ Return a list of all functions which have a unit test.  Luckily, we
        give the test files a name that matches the function.
    """

    fns = []

    p = Path(".")
    test_files = list(p.glob("**/*_test.c"))

    for f in test_files:
        fns.append(f.name.removesuffix("_test.c"))

    # Some functions have unit tests in a file that doesn't match their name.
    # This commonly happens with things like case-sensitive vs. case-insensitive
    # versions of string functions.
    fns.extend(["crm_exit_name",
                "crm_exit_str",
                "pcmk__add_separated_word",
                "pcmk__ends_with_ext",
                "pcmk__strcase_any_of",
                "pcmk_rc2exitc",
                "pcmk_rc_name",
                "pcmk_rc_str"])

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

    with open(sys.argv[1], encoding="utf-8") as lcov:
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
            new_lines.append(f"FNDA:0,{fr.name}")

        # Remove the function from the total number of functions hit.
        elif line.startswith("FNH:"):
            (_, cnt) = line.split(":")
            new_lines.append(f"FNH:{int(cnt) - 1}")

        # Reset the exection count for each line in the function to 0.
        elif is_line_in_fn(line, fr.start, fr.end):
            line = line.removeprefix("DA:")
            (line_no, cnt) = line.split(",")
            new_lines.append(f"DA:{line_no},0")

            if cnt != "0":
                executed_lines += 1

        # Remove the count of the function's executed lines from the
        # total.
        elif line.startswith("LH:"):
            (_, cnt) = line.split(":")
            new_lines.append(f"LH:{int(cnt) - executed_lines}")

        else:
            new_lines.append(line)

    return new_lines

def nothing_calls_fn(callgraph, candidates, fn):
    retval = True

    for c in candidates:
        try:
            if nx.has_path(callgraph, c.name, fn):
                retval = False
                break
        except nx.NodeNotFound as e:
            # I don't know what's up with these, so just ignore them for now.
            if c.name == "pcmk__starts_with" and fn in ["ends_with", "pcmk__str_hash", \
                                                        "pcmk__strcase_equal", "pcmk__strcase_hash", \
                                                        "copy_str_table_entry"]:
                continue

            if c.name == "pe__cmp_rsc_priority" and fn == "resource_node_score":
                continue

            raise e

    return retval

def render_record(record):
    """ Convert a record into a string that can be printed out """

    return "\n".join(record + ["end_of_record"])

def build_call_graph(file_name):
    """ Build a directed graph from function to all functions that it calls.
        Note that these graphs only cover calls within a single source file.
    """

    G = nx.DiGraph()
    regex = re.compile(r"sourcename: \"([^\"]+)\" targetname: \"([^\"]+)\"")

    with open(file_name, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("edge:"):
                continue

            match = re.search(regex, line)
            if not match:
                continue

            src = match.group(1)
            dest = match.group(2)

            # Don't know what these are, but don't care
            if match.group(2) == "__indirect_call":
                continue

            # Some keys have <file>: at the beginning, so trim that off.
            if ":" in src:
                src = src.split(":")[1]

            if ":" in dest:
                dest = dest.split(":")[1]

            G.add_edge(src, dest)

    return G

def remove_source_dir(s):
    """ Strip the current working directory (expected to be $HOME/src/pacemaker)
        from a string.
    """

    return s.removeprefix(os.getcwd() + "/")

def callgraph_files():
    """ Return a list of callgraph files generated by gcc (.ci files) as strings, with the
        current source directory removed from the front of each
    """

    lst = []
    p = Path(".")

    # Weed out stuff we don't care about:
    for fo in list(p.glob("**/*.ci")):
        s = str(fo.resolve())

        # Callgraphs for unit test files
        if fo.name.endswith("_test.ci"):
            continue

        # Callgraphs for the specially built test versions of libraries - the regular
        # ones will work fine for checking call chains
        if "_test_la-" in fo.name:
            continue

        # Callgraphs in /.libs/ (I think)
        if "/.libs/" in s:
            continue

        lst.append(remove_source_dir(s))

    return lst

def find_callgraph_file(callgraphs, file_name):
    """ Given a list of callgraph files and a source filename, return the callgraph
        file that matches or None if no callgraph is found.
    """

    # file_name is something like "lib/services/systemd.c", but the callgraph names are
    # something like "lib/services/libcrmservice_la-services.ci".  The same source file
    # could also exist in multiple directories.
    #
    # So, we need to find a callgraph file that ends with the same filename, but also
    # exists in the right subdirectory.  The presence of the compiled object in the file
    # name makes this all more annoying than it needs to be.

    # Split the directory off from the file.
    (filePath, fileFileName) = os.path.split(file_name)

    for f in callgraphs:
        # Split the directory off from the callgraph file.
        (cgPath, cgFileName) = os.path.split(f)

        # If the directories don't match, try the next.
        if filePath != cgPath:
            continue

        # Strip the extension off the file's name.
        (fileBase, _) = os.path.splitext(fileFileName)

        # If the callgraph file ends with the file (minus extension), we've
        # found a match
        if cgFileName.endswith(f"-{fileBase}.ci"):
            return f

    return None

if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isfile(sys.argv[1]):
        print(f"usage: {sys.argv[0]} <coverage_file.info>")
        sys.exit()

    tested = tested_fns()
    static = static_fns()
    records = recordize_info_file()

    callgraphs = callgraph_files()

    for r in records:
        file_name = remove_source_dir(source_file(r))
        fns = fns_in_record(r)
        public_tested_fns = [f for f in fns if f.name in tested and not is_static_fn(static, f.name)]

        cg = find_callgraph_file(callgraphs, file_name)
        if not cg:
            continue

        cg = build_call_graph(cg)

        for fr in fns:
            # If the function wasn't executed, there's nothing to do.
            if not fn_executed(r, fr.name):
                continue

            # If this is a private function with a public version we have a test
            # for, it's likely the private function does all the hard work and
            # the public test does a good enough job testing it.
            if private_fn_with_tested_public(tested, fr.name):
                # Add the private function to the list of tested functions in case
                # it calls some static function.  This ensures that static function
                # also gets its coverage counted.
                public_tested_fns.append(fr)
                continue

            # If the function is static, we won't be writing a unit test for it.
            # If no tested public function in this record calls it (we can stick
            # to just checking this record because it's static), remove its
            # coverage data.  Otherwise, leave its coverage alone.
            if is_static_fn(static, fr.name):
                if nothing_calls_fn(cg, public_tested_fns, fr.name):
                    r = erase_function_from_record(r, fr)

                continue

            # The executed public function is not in the list of functions that
            # we have a unit test for.  Remove its coverage data.
            if fr.name not in tested:
                r = erase_function_from_record(r, fr)

        print(render_record(r))
