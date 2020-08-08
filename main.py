import datetime
from datetime import datetime as dt
import glob
import logging
import json
import os
import sys
import time
import traceback

import numpy as np
import pandas as pd
from pandas.io.json import json_normalize
from sqlsorcery import MSSQL
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

from mailer import Mailer


logging.basicConfig(
    handlers=[
        logging.FileHandler(filename="app.log", mode="w+"),
        logging.StreamHandler(sys.stdout),
    ],
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %I:%M:%S%p %Z",
)


def create_driver(data_dir):
    """Create firefox webdriver with the specified preferences for automatic downloading."""
    profile = webdriver.FirefoxProfile()
    profile.set_preference("browser.download.folderList", 2)
    profile.set_preference("browser.download.manager.showWhenStarting", False)
    profile.set_preference("browser.download.dir", data_dir)
    profile.set_preference("browser.helperApps.neverAsk.saveToDisk", "text/csv")
    return webdriver.Firefox(firefox_profile=profile)


def login(driver):
    """Sign into Clever"""
    driver.get(
        "https://clever.com/oauth/authorize?channel=clever&client_id=4c63c1cf623dce82caac&confirmed=true&redirect_uri=https%3A%2F%2Fclever.com%2Fin%2Fauth_callback&response_type=code&state=8460e9c3c0026c4ef2532f43a5ab5b9bcfbadbbe516f4589544cb726fd579ecf&user_type=district_admin"
    )
    time.sleep(5)
    user_field = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, '//input[@name="username"]'))
    )
    user_field.send_keys(os.getenv("CLEVER_USER"))
    password_field = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, '//input[@name="password"]'))
    )
    password_field.send_keys(os.getenv("CLEVER_PW"))
    submit_button = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, '//button[@aria-label="Log in"]'))
    )
    submit_button.click()
    logging.info("Successfully logged in.")


def export_login_logs(driver):
    """Download Login Logs csv"""
    driver.get("https://schools.clever.com/instant-login/logs")
    time.sleep(5)
    export_button = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, '//a[@aria-label="Export as .csv"]'))
    )
    export_button.click()
    time.sleep(15)
    logging.info("Successfully downloaded login logs.")


def parse_email(df):
    """Extract email from attributes column json."""
    attributes = pd.json_normalize(df["attributes"].apply(json.loads))
    df = df.join(attributes.email)
    df.replace(np.nan, "", inplace=True)
    return df


def get_data_from_csv(sql, data_dir):
    """Get the downloaded csv and store it in a dataframe."""
    # the filename sometimes gets cut off due to a Clever bug,
    # so we're not searching for it by the .csv extension
    for filename in os.listdir(data_dir):
        if filename != ".gitkeep":
            file_path = f"{data_dir}/{filename}"
            break
    df = pd.read_csv(file_path)
    df = parse_email(df)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.astype({"timestamp": "datetime64[ns]"})
    logging.info(f"Loaded {len(df)} records from downloaded file.")
    return df


def load_newest_data(sql, df):
    """Insert newest records into the database."""
    time = sql.query(
        "SELECT TOP(1) timestamp FROM custom.Clever_LoginLogs ORDER BY timestamp DESC"
    )
    # TODO handle if table doesn't exist yet
    latest_timestamp = time["timestamp"][0]
    df = df[df["timestamp"] > latest_timestamp]
    sql.insert_into("Clever_LoginLogs", df)
    logging.info(f"Inserted {len(df)} new records into Clever_LoginLogs.")


def close(driver):
    driver.close()


def main():
    # Download data from Clever
    try:
        data_dir = os.path.join(os.getcwd(), "data")
        driver = create_driver(data_dir)
        driver.implicitly_wait(5)
        login(driver)
        export_login_logs(driver)
    finally:
        close(driver)
    # Transform and load csv data into database table
    sql = MSSQL()
    df = get_data_from_csv(sql, data_dir)
    load_newest_data(sql, df)


if __name__ == "__main__":
    try:
        main()
        error_message = None
    except Exception as e:
        logging.exception(e)
        error_message = traceback.format_exc()
    Mailer("Clever Login Logs").notify(error_message=error_message)
