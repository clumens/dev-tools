Various development tools I've come up with along the way

# mangle-coverage.py

Modify the output of lcov to remove coverage data for public functions without
unit tests.  Provide the name of the coverage file on the command line, and you
get the mangled data on stdout.  This assumes a couple things:

- You are running this from the source directory.
- You have already filtered out directories you don't care about from the lcov
  output file.
- If you care about preserving coverage data for static functions, those exist
  in the lib/ directory only.
- Your unit tests are in individual files, one per tested function, and are named
  name\_of\_function\_test.c.

Make sure the script is in your $PATH, apply mangle.patch to the pacemaker source
tree, then run `./configure --with-coverage` and `make coverage` will do the
right thing.
