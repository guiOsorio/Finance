import os
import sqlite3

from flask import Flask, flash, redirect, render_template, request, session
from flask_session import Session
from tempfile import mkdtemp
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash
from collections import defaultdict

from helpers import login_required, lookup, usd

# Configure application
app = Flask(__name__)

# Ensure templates are auto-reloaded
app.config["TEMPLATES_AUTO_RELOAD"] = True


# Ensure responses aren't cached
@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


# Custom filter
app.jinja_env.filters["usd"] = usd

# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_FILE_DIR"] = mkdtemp()
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Make sure API key is set
if not os.environ.get("API_KEY"):
    raise RuntimeError("API_KEY not set")


@app.route("/")
@login_required
def index():
    """Show portfolio of stocks"""

    user_id = session["user_id"] # store current user's id

    # Connect to database
    con = sqlite3.connect("finance.db")
    cur = con.cursor()

    # query that returns list items, each which represents a row in the table displayed for the user
    cur.execute("""SELECT SUM(shares) AS shares, company, SUM(total_amount) AS total_amount FROM transactions 
                    WHERE user_id = :id AND type = 'purchase' GROUP BY user_id, company""", {'id': user_id})
    desc = cur.description
    column_names = [col[0] for col in desc]
    user_purchase_rows = [dict(zip(column_names, row))  
             for row in cur.fetchall()]
    cur.execute("""SELECT SUM(shares) AS shares, company, SUM(total_amount) AS total_amount FROM transactions 
                    WHERE user_id = :id AND type = 'sale' GROUP BY user_id, company""", {'id': user_id})
    desc = cur.description
    column_names = [col[0] for col in desc]
    user_sale_rows = [dict(zip(column_names, row))  
             for row in cur.fetchall()]

    # send user to page to buy stocks
    if len(user_purchase_rows) == 0:
        return redirect("/buy")

    # add other necessary data to display to each dictionary in the list user_rows
    companies_qty = 0

    # populate dict of purchases with the right values
    for data_row in user_purchase_rows:
        symbol = data_row["company"]
        price = float(lookup(symbol)["price"])
        stock_value = price * data_row["shares"]
        data_row["stock_value"] = stock_value
        companies_qty += 1 # increment number of distinct companies where a purchased occured by the user

    # get the user's available cash
    cur.execute("SELECT cash FROM users WHERE id = :id", {'id': user_id})
    desc = cur.description
    row = cur.fetchone()
    cash_query = [dict(zip([c[0] for c in desc], row))]
    cash = usd(cash_query[0]["cash"])
    account_value = 0 # initialize variable to store the total account value

    # initialize variable to store user information, using a dict for each company a purchase was made,
    # each company dict will have a dict to store info specific to that company's shares
    user_rows = defaultdict(dict)

    # loop which calculates and stores data into the user_rows variable
    for i in range(companies_qty):
        for data_row in user_purchase_rows:
            user_rows[i]["symbol"] = user_purchase_rows[i]["company"] # symbol
        if len(user_sale_rows) == 0 or i > len(user_sale_rows) - 1: # if no stock sales were recorded for the user for this particular company
            user_rows[i]["shares"] = user_purchase_rows[i]["shares"] # shares
            user_rows[i]["value"] = user_purchase_rows[i]["stock_value"] # value of stock the user owns
        else:
            cur.execute("SELECT SUM(shares) AS shares FROM transactions WHERE company = :company AND type = 'sale'", {'company': user_rows[i]["symbol"]})
            desc = cur.description
            column_names = [col[0] for col in desc]
            this_shares_sales_query = [dict(zip(column_names, row))  
                 for row in cur.fetchall()]
            this_shares_sales = this_shares_sales_query[0]["shares"]
            user_rows[i]["shares"] = user_purchase_rows[i]["shares"] - this_shares_sales # shares
            share_price = lookup(user_rows[i]["symbol"])["price"]
            user_rows[i]["value"] = user_rows[i]["shares"] * share_price # value of stock the user owns
        account_value += user_rows[i]["value"] # increment the total account value by the value of shares the user owns from this company
        user_rows[i]["value"] = usd(user_rows[i]["value"]) # convert to $ format
        symbol = user_rows[i]["symbol"]
        price = float(lookup(symbol)["price"]) # get price of a share
        user_rows[i]["price"] = usd(price) # add price in $ format to dict

    # add user's available cash to the total account value and convert it to a $ format
    account_value += cash_query[0]["cash"]
    account_value = usd(account_value)

    # get the number of companies the user purchased a stock from for html iteration
    user_rows_length = len(user_rows)

    # close database connection
    con.close()

    return render_template("index.html", user_rows=user_rows, user_rows_length=user_rows_length, cash=cash, account_value=account_value)


@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():
    """Buy shares of stock"""
    if request.method == "POST":
        # handles # of shares field not being a positive integer
        num_shares = request.form.get("shares")
        if not num_shares.isnumeric():
            return render_template("error.html", errmsg="# of shares has to be an integer", errcode=409)

        user_id = session["user_id"] # store the user's id
        selected_symbol = request.form.get("symbol") # store the inputted symbol
        price = lookup(selected_symbol)["price"] # price of the stock to buy

        # query to create the table to store purchases
            # CREATE TABLE transactions (transaction_id INTEGER UNIQUE, user_id INTEGER NOT NULL, type VARCHAR(10), shares INTEGER NOT NULL,
            # company CHAR(4) NOT NULL, total_amount NUMERIC, date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            # PRIMARY KEY(transaction_id), FOREIGN KEY (user_id) REFERENCES users (id));
            # CREATE TABLE users (id INTEGER, username TEXT NOT NULL, hash TEXT NOT NULL, cash NUMERIC NOT NULL DEFAULT 10000.00, PRIMARY KEY(id));
        # Queries ran to create indexes forsignificant columns
            # CREATE INDEX type ON transactions (type);
            # CREATE INDEX shares ON transactions (shares);
            # CREATE INDEX company ON transactions (company);
            # CREATE INDEX total_amount ON transactions (total_amount);

        # Connect to database
        con = sqlite3.connect("finance.db")
        cur = con.cursor()

        cur.execute("SELECT cash FROM users WHERE id = :id", {'id': user_id}) # store the cash in the current user's account
        desc = cur.description
        row = cur.fetchone()
        user_cash = [dict(zip([c[0] for c in desc], row))]
        user_balance = user_cash[0]["cash"]
        transaction_amount = float(price) * int(num_shares) # store the total value of shares being bought
        if user_balance < transaction_amount:
            return render_template("error.html", errmsg="Not enough funds", errcode=403)
        # add row for the purchase in the purchases table
        cur.execute("INSERT INTO transactions (user_id, type, shares, company, total_amount) VALUES (?, ?, ?, ?, ?)", (user_id, "purchase", num_shares, selected_symbol, transaction_amount))
        con.commit()
        # subtract the transaction amount from the user's cash
        new_balance = user_balance - transaction_amount # user's balance after the transaction is processed
        cur.execute("UPDATE users SET cash = ? WHERE id = ?", (new_balance, user_id))
        con.commit()

        # close database connection
        con.close()

        flash("Successful transaction!")

        return redirect("/")


    return render_template("buy.html")


@app.route("/history")
@login_required
def history():
    """Show history of transactions"""

    user_id = session["user_id"] # store current user's id

    # Connect to database
    con = sqlite3.connect("finance.db")
    cur = con.cursor()

    # store all of user's past transactions in a variable
    cur.execute("SELECT type, company, (total_amount / shares) AS transaction_price, shares, date(date) AS date, strftime('%H:%M:%S', date) AS time FROM transactions WHERE user_id = ?", (user_id,))
    desc = cur.description
    column_names = [col[0] for col in desc]
    transactions = [dict(zip(column_names, row))  
             for row in cur.fetchall()]

    # iterate through transactions to update all "transaction_price" fields to be formatted in $
    for transaction in transactions:
        transaction["transaction_price"] = usd(transaction["transaction_price"])

    # close database connection
    con.close()

    return render_template("history.html", transactions=transactions)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""

    # Forget any user_id
    session.clear()

    # Connect to database
    con = sqlite3.connect("finance.db")
    cur = con.cursor()

    # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        # Ensure username was submitted
        if not request.form.get("username"):
            return render_template("error.html", errmsg="must provide username", errcode=403)

        # Ensure password was submitted
        elif not request.form.get("password"):
            return render_template("error.html", errmsg="must provide password", errcode=403)

        # Query database for username
        cur.execute("SELECT * FROM users WHERE username = ?", (request.form.get("username"),))
        rows_tuple = cur.fetchone()
        desc = cur.description
        rows = [dict(zip([c[0] for c in desc], rows_tuple))]

        # Ensure username exists and password is correct
        if len(rows) != 1 or not check_password_hash(rows[0]["hash"], request.form.get("password")):
            return render_template("error.html", errmsg="invalid username and/or password", errcode=403)

        # Remember which user has logged in
        session["user_id"] = rows[0]["id"]

        flash("Welcome!")

        # close database connection
        con.close()

        # Redirect user to home page
        return redirect("/")

    # User reached route via GET (as by clicking a link or via redirect)
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""

    # Forget any user_id
    session.clear()

    # Redirect user to login form
    return redirect("/")


@app.route("/quote", methods=["GET", "POST"])
@login_required
def quote():
    """Get stock quote."""
    if request.method == "POST":
        data = lookup(request.form.get("symbol"))
        return render_template("quoted.html", data=data)
    return render_template("quote.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Register user"""
    if request.method == "POST":

        # Connect to database
        con = sqlite3.connect("finance.db")
        cur = con.cursor()

        # handles username existing
        cur.execute("SELECT username FROM users")
        desc = cur.description
        column_names = [col[0] for col in desc]
        usernames = [dict(zip(column_names, row))  
                    for row in cur.fetchall()]
        for username in usernames:
            if username == request.form.get("username"):
                return render_template("error.html", errmsg="username already exists", errcode=409)
        # handles username or password fields being blank
        if request.form.get("username") == "" or request.form.get("password") == "" or request.form.get("confirmation") == "":
            return render_template("error.html", errmsg="input cannot be blank", errcode=411)
        # handles password and confirmation not being the same
        elif request.form.get("password") != request.form.get("confirmation"):
            return render_template("error.html", errmsg="password and confirmation do not match", errcode=409)
        # succesful registering
        else:
            hashed_password = generate_password_hash(request.form.get("password")) # hash password
            cur.execute("INSERT INTO users (username, hash) VALUES (?, ?)", (request.form.get("username"), hashed_password)) # add to database
            con.commit()
            flash("Successfully logged in!")
            # close database connection
            con.close()
            return login() #login newly registered user

    return render_template("register.html")


@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    """Sell shares of stock"""

    user_id = session["user_id"] # store current user's id

    # Connect to database
    con = sqlite3.connect("finance.db")
    cur = con.cursor()

    cur.execute("SELECT company FROM transactions WHERE user_id = ? GROUP BY company", (user_id,)) # store symbol of each stock owned by the user
    desc = cur.description
    column_names = [col[0] for col in desc]
    owned_symbols = [dict(zip(column_names, row))  
                for row in cur.fetchall()]
    # handles POST request
    if request.method == "POST":
        symbol = request.form.get("symbol")
        if symbol == None:
            return render_template("error.html", errmsg="Symbol input is invalid", errcode=409)
        else:
            pass
        shares_to_sell = int(request.form.get("shares"))
        # get user's shares
        cur.execute("SELECT SUM(shares) AS shares FROM transactions WHERE user_id = ? AND type = 'purchase' AND company = ?", (user_id, symbol))
        desc = cur.description
        column_names = [col[0] for col in desc]
        shares_purchased_query = [dict(zip(column_names, row))  
                for row in cur.fetchall()]
        shares_purchased = shares_purchased_query[0]["shares"]
        cur.execute("SELECT SUM(shares) AS shares FROM transactions WHERE user_id = ? AND type = 'sale' AND company = ?", (user_id, symbol))
        desc = cur.description
        column_names = [col[0] for col in desc]
        shares_sold_query = [dict(zip(column_names, row))  
                for row in cur.fetchall()]
        shares_sold = shares_sold_query[0]["shares"]
        if shares_sold == None:
            shares_owned = shares_purchased
        else:
            shares_owned = shares_purchased - shares_sold

        # if user doesn't have enough shares, return apology with 409
        if shares_to_sell > shares_owned:
            return render_template("error.html", errmsg="Not enough shares", errcode=409)

        # calculate transaction amount and store it in total_amount
        price_per_share = lookup(symbol)["price"]
        total_amount = round(price_per_share * shares_to_sell, 2)

        # INSERT INTO transactions
        cur.execute("INSERT INTO transactions (user_id, type, shares, company, total_amount) VALUES (?, ?, ?, ?, ?)", (user_id, 'sale', shares_to_sell, symbol, total_amount))
        con.commit()
        # UPDATE user's cash
        cur.execute("UPDATE users SET cash = (SELECT cash FROM users WHERE id = ?) + ? WHERE id = ?", (user_id, total_amount, user_id))
        con.commit()

        # close database connection
        con.close()

        flash("Successful transaction!")

        return redirect("/")

    return render_template("sell.html", symbols=owned_symbols)


def errorhandler(e):
    """Handle error"""
    if not isinstance(e, HTTPException):
        e = InternalServerError()
    return render_template("error.html", errmsg=e.name, errcode=e.code)


# Listen for errors
for code in default_exceptions:
    app.errorhandler(code)(errorhandler)



# Query returning multiple rows
        # cur.execute("SELECT * FROM users")
        # desc = cur.description
        # column_names = [col[0] for col in desc]
        # dataDict = [dict(zip(column_names, row))  
        #         for row in cur.fetchall()]
        
# Query returning one row
        # soly = request.form.get("username")
        # cur.execute("SELECT * FROM users WHERE username = :id", {'id': soly})
        # desc = cur.description
        # row = cur.fetchone()
        # rowDict = [dict(zip([c[0] for c in desc], row))]
