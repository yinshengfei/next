# Contributing

Next uses PolyForm Noncommercial 1.0.0 for public distribution and may also
be licensed through separate commercial agreements. Please read [LICENSE](LICENSE),
[NOTICE](NOTICE), and [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md).

## Rights review before merge

Only submit material you created or are authorized to contribute. Identify
third-party code/assets and their licenses; do not submit credentials or
personal task data. Employer-owned work requires the employer's permission.

The public license alone does not authorize the maintainer to commercially
relicense someone else's contribution. Before merging a copyrightable
contribution, the maintainer must obtain an explicit written agreement
covering both licensing paths from the relevant rights holder(s). A DCO
sign-off or the act of opening a pull request is not a substitute.

For a contribution you own and are authorized to license, you may explicitly
post the following statement in the pull request, identifying the covered
commit(s). Alternatively, arrange a separate signed agreement with the
maintainer. Do not post the statement if it is not accurate.

> I identify the commits covered by this agreement in this pull request.
> I have the authority to grant the following rights for my contributions
> in those commits. I retain ownership and grant Next maintainer yinshengfei
> a perpetual, worldwide, non-exclusive, royalty-free, irrevocable copyright
> license to use, reproduce, modify, prepare derivative works of, publicly
> display, perform, distribute, and sublicense those contributions under
> PolyForm Noncommercial 1.0.0 and separate commercial licenses. To the
> extent I control patent claims necessarily infringed by my contributions,
> I grant the same maintainer a perpetual, worldwide, non-exclusive,
> royalty-free patent license, with the right to sublicense, to make, have
> made, use, offer to sell, sell, and import those contributions. I have
> identified any third-party material and applicable license restrictions.

This process applies to future contributions. It does not retroactively
establish permission for earlier contributions. The maintainer must retain
records of prior contributors' permissions and exclude or resolve any
contribution whose rights are uncertain before issuing a commercial license.

## Local checks

Run `.venv/bin/python -m pytest -q` from the project root. Tests use simulated
services; do not include real account data in fixtures.
