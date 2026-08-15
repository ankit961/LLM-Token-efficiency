"""Phase 2.4-C -- the conservative Bash recognizer (normalize.bash_effects).

Only unambiguous read/mutation forms are recognized; anything with shell structure is `unknown`
(never exploration). git show REV:PATH is a git_blob (historical), not the worktree file.
"""
from contextruntime.normalize import bash_effects, bash_parse


def _ke(effects):
    return [(e.kind, e.path) for e in effects]


# --- hook_schema 0.4.1 evidence-integrity regressions (from independent review) ---
def test_conditional_and_background_do_not_manufacture_effects():
    # a statement after && / || / & is NOT guaranteed to run -> no phantom read/edit
    assert bash_effects("false && cat never.py") == []
    assert bash_effects("true || cat never.py") == []
    assert bash_effects("grep x a.py & cat b.py") == []          # both adjacent to & -> uncertain
    p = bash_parse("false && cat never.py")
    assert p.conditional is True and p.effects == []


def test_cd_updates_virtual_cwd_for_subsequent_paths():
    # cd DIR && CMD: the cd guard makes CMD certain AND paths resolve against DIR
    e = bash_effects("cd tests && grep x urls.py")
    assert len(e) == 1 and e[0].kind == "read" and e[0].path == "tests/urls.py"
    assert bash_effects("cd a/b ; cat c.py")[0].path == "a/b/c.py"


def test_composite_read_is_not_attributable():
    # grep + execution (or grep + unknown) share ONE Bash response -> the read is not attributable
    p = bash_parse("grep x a.py ; pytest -q")
    reads = [e for e in p.effects if e.kind == "read"]
    assert len(reads) == 1 and reads[0].attributable is False and p.has_execution is True
    p2 = bash_parse("grep x a.py ; mystery_reader b.py")
    assert [e for e in p2.effects if e.kind == "read"][0].attributable is False
    assert p2.coverage() == "partial" and p2.unknown_statements == 1     # partial recognition kept
    # a clean single read IS attributable
    assert bash_parse("grep foo a.py").effects[0].attributable is True


def test_command_family_scope_and_derived_pipelines():
    assert bash_effects("find . -name '*.py'")[0].path == "."         # not '*.py'
    assert bash_effects("git grep needle")[0].path == "."             # not 'needle'
    assert bash_effects("cat a.py | wc -l")[0].representation == "derived"   # a count, not source
    assert bash_effects("cat a.py | grep needle")[0].representation == "search"  # filtered source


def test_read_cat_multiple_files():
    assert _ke(bash_effects("cat a.py b.py")) == [("read", "a.py"), ("read", "b.py")]


def test_head_tail_sed_quiet_are_reads():
    assert _ke(bash_effects("head -n 5 a.py")) == [("read", "a.py")]
    assert _ke(bash_effects("tail -f log.txt")) == [("read", "log.txt")]
    assert _ke(bash_effects("sed -n 1,5p a.py")) == [("read", "a.py")]


def test_redirection_tee_sed_inplace_are_mutations():
    assert _ke(bash_effects("echo hi > out.txt")) == [("edit", "out.txt")]
    assert _ke(bash_effects("echo hi >> out.txt")) == [("edit", "out.txt")]
    assert _ke(bash_effects("printf x | tee f")) == []       # unknown-only leading (printf) -> no asserted effect
    assert _ke(bash_effects("tee f.txt")) == [("edit", "f.txt")]
    assert _ke(bash_effects("sed -i s/a/b/ f.py")) == [("edit", "f.py")]


def test_cp_mv_rm():
    # cp's source bytes are NOT shown to the model, so cp is a destination mutation only (no read)
    assert _ke(bash_effects("cp a.py b.py")) == [("edit", "b.py")]
    assert _ke(bash_effects("mv a.py b.py")) == [("edit", "a.py"), ("edit", "b.py")]
    assert _ke(bash_effects("rm a.py")) == [("edit", "a.py")]


def test_git_show_is_a_git_blob_not_worktree():
    e = bash_effects("git show HEAD~2:src/foo.py")
    assert e[0].kind == "read" and e[0].path == "src/foo.py"
    assert e[0].representation == "git_blob" and e[0].ref == "HEAD~2"


def test_genuinely_complex_shell_is_unknown_never_exploration():
    # truly-complex shell (subshell / loop / xargs) is still unknown. Compound ;/&&/|| is SPLIT and
    # recognized per statement (tested below), so it is NOT in this list.
    for cmd in ["for f in *; do cat $f; done", "cat $(ls)", "grep x $(ls)", "a.sh && b.sh",
                "weird thing", "xargs cat < list"]:
        assert all(x.kind == "unknown" for x in bash_effects(cmd)), cmd


def test_compound_statements_are_split_and_recognized():
    # cd t && grep x f  -> the grep read (cd is a no-op); grep;grep -> two reads; a pwd;ls -> the ls
    assert [x.representation for x in bash_effects("cd tests && grep -n '@' urls.txt")] == ["search"]
    assert len(bash_effects("grep -n a f | head; grep -n b f")) == 2
    assert [x.representation for x in bash_effects("pwd; ls django/")] == ["path_listing"]
    # cat a; cat b -> two file reads (each statement is a clean file materialization)
    e = bash_effects("cat a.py; cat b.py")
    assert [x.kind for x in e] == ["read", "read"] and {x.representation for x in e} == {"file"}


# --- hook_schema 0.4.0: representation-typed shell ---
def test_grep_is_search_materialization():
    e = bash_effects("grep -rn 'needle' django/utils/")
    assert len(e) == 1 and e[0].kind == "read" and e[0].representation == "search"
    e2 = bash_effects("git grep needle")
    assert e2[0].kind == "read" and e2[0].representation == "search" and e2[0].note == "git_grep"
    e3 = bash_effects("rg pattern src")
    assert e3[0].kind == "read" and e3[0].representation == "search"


def test_find_and_ls_are_path_listing():
    for cmd in ["find . -name '*.py'", "ls django/db/", "find django -type f"]:
        e = bash_effects(cmd)
        assert e[0].kind == "read" and e[0].representation == "path_listing", cmd


def test_execution_is_recognized_not_a_read_or_unknown():
    for cmd in ["python -c 'open(1)'", "python tests/runtests.py auth", "./manage.py test",
                "pytest tests/", "tox -e py39", "make", "flake8 django/", "pip install -e ."]:
        e = bash_effects(cmd)
        assert e and all(x.kind == "execution" for x in e), cmd


def test_leading_pipe_materializes_filtered_subset_as_search():
    # `cat a.py | grep x` shows the model a FILTERED subset -> a search (derived) read of a.py, NOT a
    # full-file read (so it never claims a full-file content_version).
    e = bash_effects("cat a.py | grep x")
    assert len(e) == 1 and e[0].kind == "read" and e[0].path == "a.py"
    assert e[0].representation == "search"                      # filtered, not "file"
    # a piped grep is still search
    assert bash_effects("grep pat dir | head")[0].representation == "search"


def test_redirected_grep_is_an_edit_not_a_read():
    # `grep pat foo > out` sends bytes to a file, not the model -> edit of out, never a search read.
    e = bash_effects("grep pat foo > out.txt")
    assert e[0].kind == "edit" and e[0].path == "out.txt"


# --- 0.4.0 adversarial-audit regressions ---
def test_stderr_redirect_does_not_fake_an_edit_or_drop_the_read():
    # `cmd 2>/dev/null` / `2>&1` redirect STDERR -- stdout still reaches the model, so the read stands
    # and NO edit of /dev/null is fabricated (the HIGH compound-split bug).
    assert bash_effects("cat config.py 2>/dev/null") == [
        bash_effects("cat config.py 2>/dev/null")[0]]  # one effect
    e = bash_effects("cat config.py 2>/dev/null")
    assert e[0].kind == "read" and e[0].path == "config.py" and e[0].representation == "file"
    assert bash_effects("grep -rn TODO src/ 2>/dev/null")[0].representation == "search"
    assert bash_effects("ls -la 2>&1")[0].kind == "read"        # 2>&1 not split at the &


def test_quoted_metacharacters_are_not_operators():
    # a quoted separator/redirect is a grep PATTERN, not shell structure
    assert bash_effects("grep '>' file.py")[0].kind == "read"   # not an edit
    assert bash_effects("grep '&&' a.py")[0].kind == "read"     # not split into a phantom execution
    assert bash_effects("grep ';' f.py")[0].kind == "read"


def test_execution_only_on_program_not_operand():
    # `cat manage.py` READS manage.py -- it must not be flagged execution by the operand name
    assert bash_effects("cat manage.py")[0].kind == "read"
    assert bash_effects("git show HEAD:runtests.py")[0].representation == "git_blob"
    assert bash_effects("grep -n def manage.py")[0].kind == "read"
    assert bash_effects("./manage.py test")[0].kind == "execution"   # program IS manage.py -> execution


def test_find_mutation_is_not_a_read():
    for cmd in ["find . -name '*.pyc' -delete", "find build -delete",
                "find . -type f -exec rm {} +", "find . -name x -execdir rm {} ;"]:
        assert all(e.kind == "unknown" for e in bash_effects(cmd)), cmd
    # a plain find is still a path_listing read
    assert bash_effects("find . -name '*.py'")[0].representation == "path_listing"
