import sys
import os
import shutil
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt
import traceback

# Import necessary libraries for album art downloading
try:
    import requests
    from itunespy import search_album
except ImportError:
    print("Required libraries 'requests' and 'itunespy' are not installed.")
    print("Please install them with: pip install requests itunespy")
    sys.exit(1)

def get_album_art(artist_name: str, album_name: str, output_dir: str = "album_art"):
    """
    Searches for an album's cover art and saves a high-resolution version
    to a specified directory.
    
    Returns the filepath of the downloaded image on success.
    """
    try:
        print(f"Searching for album art for '{album_name}' by '{artist_name}'...")
        albums = search_album(album_name)

        if not albums:
            print(f"Error: Could not find the album '{album_name}'.")
            return None

        album = albums[0]
        original_art_url = album.artworkUrl100
        high_res_url = original_art_url.replace('100x100bb', '800x800bb')
        print(f"Found album art URL: {high_res_url}")

        image_response = requests.get(high_res_url, stream=True)
        image_response.raise_for_status()

        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        filename = f"{artist_name} - {album_name}.jpg".replace("/", "-").replace("\\", "-")
        filepath = os.path.join(output_dir, filename)

        with open(filepath, 'wb') as f:
            for chunk in image_response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"Successfully saved high-resolution album art to '{filepath}'")
        return filepath

    except requests.exceptions.RequestException as e:
        print(f"Error downloading the image: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        traceback.print_exc()
        return None

class ImageProcessorApp(QWidget):
    """A PyQt6 application for viewing and processing images from a list."""

    def __init__(self, image_paths):
        super().__init__()
        self.image_paths = image_paths
        self.current_image_index = 0
        self.downloaded_image_path = None
        self.setWindowTitle("Image Processor")
        self.setGeometry(50, 50, 1200, 700)
        self.init_ui()
        self.load_images()

    def init_ui(self):
        """Initializes the user interface components."""
        main_layout = QVBoxLayout()
        image_layout = QHBoxLayout()
        
        self.info_label = QLabel("Artist: N/A | Album: N/A")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setStyleSheet("font-size: 14pt; font-weight: bold;")
        
        left_vertical_layout = QVBoxLayout()
        self.left_image_label = QLabel("Original Album Art")
        self.left_image_label.setFixedSize(550, 550)
        self.left_image_label.setStyleSheet("background-color: lightgray; border: 1px solid black;")
        self.left_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.left_dimensions_label = QLabel("Dimensions: N/A")
        self.left_dimensions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_vertical_layout.addWidget(self.left_image_label)
        left_vertical_layout.addWidget(self.left_dimensions_label)

        right_vertical_layout = QVBoxLayout()
        self.right_image_label = QLabel("High-Res Album Art")
        self.right_image_label.setFixedSize(550, 550)
        self.right_image_label.setStyleSheet("background-color: darkgray; border: 1px solid black;")
        self.right_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.right_dimensions_label = QLabel("Dimensions: N/A")
        self.right_dimensions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_vertical_layout.addWidget(self.right_image_label)
        right_vertical_layout.addWidget(self.right_dimensions_label)

        image_layout.addLayout(left_vertical_layout)
        image_layout.addLayout(right_vertical_layout)

        button_layout = QHBoxLayout()
        self.skip_button = QPushButton("Skip")
        self.skip_button.clicked.connect(self.on_skip)
        
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.on_save)

        button_layout.addWidget(self.skip_button)
        button_layout.addWidget(self.save_button)

        main_layout.addWidget(self.info_label)
        main_layout.addLayout(image_layout)
        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def load_images(self):
        """Loads the next set of images and displays them with dimensions."""
        if self.current_image_index >= len(self.image_paths):
            QMessageBox.information(self, "End of List", "All images have been processed.")
            self.close()
            return
            
        local_path = self.image_paths[self.current_image_index]
        self.downloaded_image_path = None
        
        # Clear previous images and dimensions
        self.left_image_label.clear()
        self.right_image_label.clear()
        self.left_dimensions_label.setText("Dimensions: N/A")
        self.right_dimensions_label.setText("Dimensions: N/A")
        self.info_label.setText("Artist: N/A | Album: N/A")

        # Load and display the local image on the left
        try:
            if not os.path.exists(local_path):
                raise FileNotFoundError(f"File does not exist: {local_path}")
            pixmap = QPixmap(local_path)
            if pixmap.isNull():
                raise ValueError(f"Could not load image from path: {local_path}")
            self.left_dimensions_label.setText(f"Dimensions: {pixmap.width()}x{pixmap.height()}")
            pixmap = pixmap.scaled(self.left_image_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.left_image_label.setPixmap(pixmap)
        except (IOError, FileNotFoundError, ValueError) as e:
            # If the local image can't be loaded, skip to the next one
            QMessageBox.warning(self, "Error", f"Failed to load local image:\n{e}\nSkipping...")
            self.on_skip()
            return

        # Extract artist and album names from the path
        path_parts = os.path.normpath(local_path).split(os.sep)
        if len(path_parts) >= 3:
            artist_name = path_parts[-3]
            album_name = path_parts[-2]
            self.info_label.setText(f"Artist: {artist_name} | Album: {album_name}")
            
            # Download and display the high-res album art on the right
            downloaded_path = get_album_art(artist_name, album_name)
            if downloaded_path and os.path.exists(downloaded_path):
                self.downloaded_image_path = downloaded_path
                try:
                    pixmap = QPixmap(downloaded_path)
                    if pixmap.isNull():
                        raise ValueError(f"Could not load downloaded image: {downloaded_path}")
                    self.right_dimensions_label.setText(f"Dimensions: {pixmap.width()}x{pixmap.height()}")
                    pixmap = pixmap.scaled(self.right_image_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    self.right_image_label.setPixmap(pixmap)
                except (IOError, FileNotFoundError, ValueError) as e:
                    # If the downloaded image can't be displayed, skip
                    QMessageBox.warning(self, "Error", f"Failed to display downloaded image:\n{e}\nSkipping...")
                    self.on_skip()
            else:
                # If high-res art is not found or downloaded, skip automatically
                print("High-res album art not found or downloaded. Skipping...")
                self.on_skip()
        else:
            # If artist/album can't be parsed, skip automatically
            print("Could not parse artist/album from path. Skipping...")
            self.on_skip()

    def on_skip(self):
        """Handles the 'Skip' button click."""
        self.current_image_index += 1
        self.load_images()

    def on_save(self):
        """Handles the 'Save' button, overwriting the original file without a success dialog."""
        if self.current_image_index < len(self.image_paths):
            original_path = self.image_paths[self.current_image_index]
            
            if self.downloaded_image_path and os.path.exists(self.downloaded_image_path):
                try:
                    shutil.copy(self.downloaded_image_path, original_path)
                    print(f"Successfully saved high-res image to: {original_path}")
                    self.on_skip()
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to save image:\n{e}")
            else:
                QMessageBox.warning(self, "Save Error", "No high-resolution image to save.")
                self.on_skip()

def main():
    """Main function to handle command-line arguments and start the application."""
    if len(sys.argv) < 2:
        print("Usage: python main.py <path_to_text_file>")
        sys.exit(1)

    file_path = sys.argv[1]

    try:
        with open(file_path, 'r') as f:
            image_paths = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Error: The file '{file_path}' was not found.")
        sys.exit(1)
    except Exception as e:
        print(f"An error occurred while reading the file: {e}")
        sys.exit(1)

    if not image_paths:
        print("The input file does not contain any image paths.")
        sys.exit(1)

    app = QApplication(sys.argv)
    ex = ImageProcessorApp(image_paths)
    ex.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
