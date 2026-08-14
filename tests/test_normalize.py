"""Phase 2.4-C -- the conservative Bash recognizer (normalize.bash_effects).

Only unambiguous read/mutation forms are recognized; anything with shell structure is `unknown`
(never exploration). git show REV:PATH is a git_blob (historical), not the worktree file.
"""
from contextruntime.normalize import bash_effects


def _ke(effects):
    return [(e.kind, e.path) for e in effects]


def test_read_cat_multiple_files():
    assert _ke(bash_effects("cat a.py b.py")) == [("read", "a.py"), ("read", "b.py")]


def test_head_tail_sed_quiet_are_reads():
    assert _ke(bash_effects("head -n 5 a.py")) == [("read", "a.py")]
    assert _ke(bash_effects("tail -f log.txt")) == [("read", "log.txt")]
    assert _ke(bash_effects("sed -n 1,5p a.py")) == [("read", "a.py")]


def test_redirection_tee_sed_inplace_are_mutations():
    assert _ke(bash_effects("echo hi > out.txt")) == [("edit", "out.txt")]
    assert _ke(bash_effects("echo hi >> out.txt")) == [("edit", "out.txt")]
    assert _ke(bash_effects("printf x | tee f")) == [("unknown", "")]      # pipe -> unknown, not tee
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


def test_complex_shell_is_unknown_never_exploration():
    for cmd in ["cat a.py | grep x", "for f in *; do cat $f; done", "cat $(ls)",
                "a.sh && b.sh", "cat a; cat b", "python -c 'open(1)'"]:
        assert all(x.kind == "unknown" for x in bash_effects(cmd)), cmd
