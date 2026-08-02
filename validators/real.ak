test compares_two_things() {
  balance_after == balance_before + amount
}

test calls_the_validator() {
  spend(mk_datum(1000), Void, mk_ctx())
}

test multiline_and_real() {
  let result = spend(mk_datum(1), Void, mk_ctx())
  result == True
}

test negative_case() fail {
  spend(mk_datum(0), Void, mk_ctx())
}

test looks_like_an_assertion() {
  owner == owner
}
