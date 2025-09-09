#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">3.0"
# dependencies = [
#     "selenium"
# ]
# ///

# todo:
#  - sort by image size (tineye sort doesn't seem to be reliable)
#  - allow user to choose between images
#  - save / replace image(s)

import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

def get_highest_resolution_urls(image_path):
    """
    Searches TinEye for the highest resolution versions of an image,
    using headless Chrome to bypass potential driver issues.
    """
    if not os.path.exists(image_path):
        print(f"Error: The image file at {image_path} was not found.")
        return []

    # Configure Chrome to run in headless mode
    chrome_options = Options()
    #chrome_options.add_argument("--headless")
    
    # Use Selenium Manager to automatically get the correct chromedriver
    # and pass the headless options to the driver
    service = Service()
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.get("https://tineye.com/")

    try:
        # Find the file upload input and upload the image.
        upload_input = driver.find_element(By.CSS_SELECTOR, "input[type='file']")
        upload_input.send_keys(os.path.abspath(image_path))
        # Wait for the search results page to load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".flex .items-start"))
        )
        driver.get(driver.current_url.replace('sort=score', 'sort=size'))
        # Wait for the search results page to load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".flex .items-start"))
        )
        # Find all result links and extract their URLs.
        image_links = driver.find_elements(By.CSS_SELECTOR, "div.w-full p.text-sm span.text-matterhorn-grey a.font-semibold")

        urls = [link.get_attribute("href") for link in image_links]
        return urls

    except Exception as e:
        print(f"An error occurred: {e}")
        return []

    finally:
        driver.quit()

# Example usage with the specified image file name.
if __name__ == "__main__":
    image_to_search = "Zappa_Roxy_&_Elsewhere.jpg"
    results = get_highest_resolution_urls(image_to_search)
    if results:
        print("Found the following URLs for highest resolution images:")
        for url in results:
            print(url)
    else:
        print("No results found or an error occurred.")
