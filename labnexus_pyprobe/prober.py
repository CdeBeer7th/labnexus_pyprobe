import argparse
import os
from datetime import datetime

import requests
import platform
from win10toast import ToastNotifier



def FileWatcher(dir_path, server_url):

    os_name = platform.system()
    logged = False

    if os_name == "Linux":
        while not logged:

            username = input("Enter your LabNexus email here:")
            password = input("Enter your account password here:")
            try:
                response = requests.post(
                    f"http://{server_url}/auth/jwt/login",
                    data={"username": username, "password": password},
                )
                print(response)
                token = response.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                print(
                    "Stored token, pyProbe has successfully opened a session with the server.",
                )
                
                logged = True
            except Exception as e:
                
                print(e)

        # Set the headers for the PUT request (if needed)
        # headers = {'Content-Type': 'application/octet-stream'}
                
        last_processed_files = []
        while logged:
            # Keep track of the last processed file
            # dir_path = Path(dir_path)
            while True:
                print("Checking for new files in directory:", dir_path)
                
                # Get a list of all files in the directory
                files = os.listdir(dir_path)
                # Sort files by modification time (newest first)
                files.sort(
                    key=lambda x: os.path.getmtime(os.path.join(dir_path, x)),
                    reverse=True,
                )

                # Check for new files
                for file_name in files:
                    file_path = os.path.join(dir_path, file_name)

                    # Skip the last processed file and directories
                    if file_name in last_processed_files or os.path.isdir(file_path):
                        print("Skipping", file_name)
                        continue

                    try:
                        # Open the file in binary mode
                        
                        print("Found new file, uploading", file_name)
                        print(file_path)
                        with open(file_path, "rb") as file:
                            # Send a PUT request to the server with the file data
                            response = requests.post(
                                f"http://{server_url}/files/upload/pyprobe",
                                files={"file": file},
                                headers=headers,
                            )

                        # Check if the request was successful
                        if response.status_code == 200:
                            
                            print(
                                f"File {file_name} uploaded successfully at {datetime.now()}",
                            )
                            last_processed_files.append(file_name)
                        else:
                            print(
                                f"Error uploading file {file_name}: {response.status_code} - {response.text}",
                            )
                        
                    except Exception as e:
                        print(f"Error opening/processing file {file_name}: {e}")
                        
                    # Exit the loop after processing the newest file
                    break

                # Wait for a specified time before checking again
                # You can adjust the sleep time based on your requirements
                import time

                time.sleep(5)

    elif os_name == "Windows":
        
        toaster = ToastNotifier()
        toaster.show_toast(
                    "pyProbe",  # Notification title
                    "pyProbe is running...",  # Notification message
                #  icon_path="path\to\your\icon.ico"
                )
        
        logged = False

        while not logged:

            username = input("Enter your LabNexus email here:")
            password = input("Enter your account password here:")
            try:
                response = requests.post(
                    f"http://{server_url}/auth/jwt/login",
                    data={"username": username, "password": password},
                )
                print(response)
                token = response.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                print(
                    "Stored token, pyProbe has successfully opened a session with the server.",
                )
                toaster.show_toast(
                    "pyProbe",  # Notification title
                    "Stored token, pyProbe has successfully opened a session with the server.",  # Notification message
                #  icon_path="path\to\your\icon.ico"
                )
                logged = True
            except Exception as e:
                toaster.show_toast(
                    "pyProbe",  # Notification title
                    str(e)
                #  icon_path="path\to\your\icon.ico"
                )
                print(e)

        # Set the headers for the PUT request (if needed)
        # headers = {'Content-Type': 'application/octet-stream'}
        last_processed_files = []
        while logged:
            # Keep track of the last processed file
            # dir_path = Path(dir_path)
            while True:
                print("Checking for new files in directory:", dir_path)
                toaster.show_toast(
                    "pyProbe - Starting up",  # Notification title
                    f"Checking for new files in directory: {dir_path}",  # Notification message
                #  icon_path="path\to\your\icon.ico"
                )
                # Get a list of all files in the directory
                files = os.listdir(dir_path)
                # Sort files by modification time (newest first)
                files.sort(
                    key=lambda x: os.path.getmtime(os.path.join(dir_path, x)),
                    reverse=True,
                )

                # Check for new files
                for file_name in files:
                    file_path = os.path.join(dir_path, file_name)

                    # Skip the last processed file and directories
                    if file_name in last_processed_files or os.path.isdir(file_path):
                        print("Skipping", file_name)
                        continue

                    try:
                        # Open the file in binary mode
                        toaster.show_toast(
                            "pyProbe - New File Found",  # Notification title
                            f"Found new file: {file_name}, uploading to server..."
                        #  icon_path="path\to\your\icon.ico"
                        )
                        print("Found new file, uploading", file_name)
                        print(file_path)
                        with open(file_path, "rb") as file:
                            # Send a PUT request to the server with the file data
                            response = requests.post(
                                f"http://{server_url}/files/upload/pyprobe",
                                files={"file": file},
                                headers=headers,
                            )

                        # Check if the request was successful
                        if response.status_code == 200:
                            toaster.show_toast(
                                "pyProbe - Upload Success",  # Notification title
                                f"File {file_name} uploaded successfully at {datetime.now()}"
                                #  icon_path="path\to\your\icon.ico"
                            )
                            print(
                                f"File {file_name} uploaded successfully at {datetime.now()}",
                            )
                            last_processed_files.append(file_name)
                        else:
                            print(
                                f"Error uploading file {file_name}: {response.status_code} - {response.text}",
                            )
                            toaster.show_toast(
                                "pyProbe - Upload Error",  # Notification title
                                f"Error uploading file {file_name}: {response.status_code} - {response.text}"
                                #  icon_path="path\to\your\icon.ico"
                            )
                    except Exception as e:
                        print(f"Error opening/processing file {file_name}: {e}")
                        toaster.show_toast(
                                "pyProbe - Processing Error",  # Notification title
                                f"Error opening/processing file: {file_name}. Please check data integrity. Skipping for now..."
                                #  icon_path="path\to\your\icon.ico"
                            )
                    # Exit the loop after processing the newest file
                    break

                # Wait for a specified time before checking again
                # You can adjust the sleep time based on your requirements
                import time

                time.sleep(5)
    else:
        print(f"Unsupported OS: {os_name}")


"""
This module implements a file watcher that monitors a directory for new files
and sends them to a LabNexus server.
"""


def main():
        
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "directory",
        help="the path to the directory you want to monitor",
    )
    parser.add_argument(
        "server",
        help="the URL of the LabNexus server (with port if needed)",
    )
    args = parser.parse_args()

    probe_path = args.directory
    server_domain = args.server

    FileWatcher(dir_path=probe_path, server_url=server_domain)

