function validate() {
    name = $('#name').val()
    voter_card_raw = $('#voter_card').val()
    voter_card = voter_card_raw.replace(/[^A-Za-z0-9]/g, '')
    $('#voter_card').val(voter_card.toUpperCase())
    password = $('#password').val()
    warning = $('#warning')
    if(name == '' || voter_card == '' || password == ''){
        warning.html("Please fill all the fields")
        glowWarning()
        return false
    }
    var voter_card_regex = /^[A-Za-z]{3}[0-9]{7}$/
    if(voter_card_regex.test(voter_card)){
        return true
    }
    warning.html("Invalid Voter Card Number (format: XXX1234567)")
    glowWarning()
    return false
}

function glowWarning() {
    var warning = $('#warning')
    warning.css(
        'text-shadow',
        '0 0 50px red, 0 0 20px red, 0 0 10px red')
    setTimeout(function() {
        warning.css('text-shadow', 'none')
    }, 300)
}