import os
import re
import crypt
import shutil
import requests
import subprocess

from textwrap import dedent
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = b'5rQl7PbSGn2A0_UOzBKsAw'

@app.template_filter('config_rows')
def config_rows(config_content):
    return len(config_content.readlines())

def list_nginx_configurations():
    nginx_configurations = []
    nginx_config_dir = '/etc/nginx/sites-available'
    
    for filename in os.listdir(nginx_config_dir):
        nginx_configurations.append(filename)
    
    # alpha sort
    nginx_configurations = sorted(nginx_configurations)

    return nginx_configurations

def test_nginx():
    try:
        nginx_test = subprocess.run(['nginx', '-t'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return nginx_test.returncode == 0
    except Exception as e:
        return False

def reload_nginx():
    try:
        subprocess.run(['systemctl', 'reload', 'nginx'])
        return True
    except Exception as e:
        return False

def test_and_reload_nginx():
    if test_nginx() and reload_nginx():
        return True

    return False

def delete_config_file(config_name):
    config_path = f'/etc/nginx/sites-available/{config_name}'

    try:
        # Check if the configuration file exists
        if os.path.exists(config_path):
            # Delete the configuration file
            os.remove(config_path)
    except Exception as e:
        flash(f'Failed to delete {config_name} configuration file. Error: {str(e)}', 'danger')

# Utility function to delete a symlink
def delete_symlink(config_name):
    symlink_path = f'/etc/nginx/sites-enabled/{config_name}'

    try:
        # Check if the symlink exists
        if os.path.exists(symlink_path):
            # Delete the symlink
            os.remove(symlink_path)
    except Exception as e:
        flash(f'Failed to delete {config_name} symlink. Error: {str(e)}', 'danger')

def delete_config_and_symlink(config_name):
    # order of operations matter here
    # symlink must be removed first
    delete_symlink(config_name)
    delete_config_file(config_name)

def paginate_configurations(configurations, page, per_page):
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    return configurations[start_idx:end_idx]

def filter_configurations(configurations, search_term):
    # Implement logic to filter configurations based on search_term
    filtered = [config for config in configurations if search_term in config]
    return filtered

@app.route('/')
def index():
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=15, type=int)
    search_term = request.args.get('search', '')

    nginx_configurations = list_nginx_configurations()
    
    # Filter configurations based on the search term
    filtered_configurations = filter_configurations(nginx_configurations, search_term)
    
    total_configurations = len(filtered_configurations)

    # Paginate the filtered configurations
    configurations = paginate_configurations(filtered_configurations, page, per_page)

    return render_template('index.html', configurations=configurations, page=page, per_page=per_page, total_configurations=total_configurations, search_term=search_term)

@app.route('/edit/<config_name>', methods=['GET', 'POST'])
def edit(config_name):
    config_path = f'/etc/nginx/sites-available/{config_name}'
    server_dns = None

    # Check if the file exists
    if os.path.exists(config_path):
        with open(config_path, 'r') as config_file:
            nginx_config_content = config_file.read()

            # Use regular expressions to extract the DNS name
            match = re.search(r'server\s+([\w.-]+)(:\d+)?;', nginx_config_content)
            if match:
                server_dns = match.group(1)

    if request.method == 'POST':
        edited_content = request.form.get('edited_content')
        with open(config_path, 'w') as config_file:
            config_file.write(edited_content)
        
        if test_and_reload_nginx():
            flash(f'Successfully updated {config_name} configuration. Configuration has been applied to Nginx.', 'success')
        else:
            flash(f'Failed to update {config_name} configuration. Nginx configuration test failed.', 'danger')
        

    with open(config_path, 'r') as config_file:
        config_content = config_file.read()

    config_rows = config_content.count('\n') + 1

    return render_template('edit_config.html', filename=config_name, config_content=config_content, server_dns=server_dns, config_rows=config_rows)

@app.route('/delete/<config_name>')
def delete(config_name):
    try:
        delete_config_and_symlink(config_name)

        # Test Nginx configuration and reload if successful
        if test_and_reload_nginx():
            flash(f'Successfully deleted {config_name} configuration.', 'success')
        else:
            flash(f'Failed to delete {config_name} configuration. Nginx configuration test failed.', 'danger')

        # Redirect back to the index page after deletion
        return redirect(url_for('index'))
    except Exception as e:
        # Handle any errors that may occur during deletion
        flash(f'Failed to delete {config_name} configuration. Error: {str(e)}', 'danger')
        return redirect(url_for('index'))

@app.route('/create', methods=['GET', 'POST'])
def create():
    config_name = None  # Initialize config_name to None
    
    try:
        if request.method == 'POST':
            server_address = request.form.get('server_address')
            server_name = request.form.get('server_name')

            # Save the configuration to /etc/nginx/sites-available/
            config_name = f'{server_name}'
            config_path = os.path.join('/etc/nginx/sites-available', config_name)

            # Check if the configuration file already exists
            existing_config_path = os.path.join('/etc/nginx/sites-available', server_name)
            if os.path.exists(existing_config_path):
                flash(f'Configuration for {server_name} already exists.', 'warning')
                return redirect(url_for('create'))

            # Generate the Nginx configuration content
            nginx_config_content = dedent(f'''
            upstream {server_name} {{
                server {server_address}:443;
            }}
            
            server {{
                server_name {server_name}.hostedbroadcasting.com;

                listen 80;
                listen 443 ssl;

                ssl on;
                ssl_certificate /etc/nginx/ssl_certs/{server_name}.crt;
                ssl_certificate_key /etc/nginx/ssl_certs/{server_name}.key;
                ssl_session_cache builtin:1000 shared:SSL:10m;
                ssl_protocols TLSv1 TLSv1.1 TLSv1.2;
                ssl_prefer_server_ciphers on;

                access_log /var/log/nginx/{server_name}.access.log;
                error_log  /var/log/nginx/{server_name}.error.log warn;

                location / {{
                    proxy_pass https://{server_name}.teve.inc;
                    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                    proxy_set_header Host $http_host;
                    proxy_read_timeout 600s;
                }}
            }}
            ''').strip('\n\r')
            
            with open(config_path, 'w') as config_file:
                config_file.write(nginx_config_content)
            
            # Create a symlink to /etc/nginx/sites-enabled/
            enabled_link_path = os.path.join('/etc/nginx/sites-enabled', config_name)
            os.symlink(config_path, enabled_link_path)
            
            # Test Nginx configuration and reload if successful
            if test_and_reload_nginx():
                flash(f'Successfully created configuration for {config_name}. Configuration has been applied to Nginx.', 'success')
            else:
                flash(f'Failed to create {config_name} configuration. Nginx configuration test failed.', 'danger')
        
    except Exception as e:
        # Handle any other errors that may occur during creation
        flash(f'Error: {str(e)}', 'danger')
        
    return render_template('create_config.html')

@app.route('/status')
def nginx_status():
    # Replace with the actual URL of your Nginx stub_status page
    nginx_status_url = 'http://localhost/nginx-status'
    
    try:
        # Fetch the raw content from the Nginx stub_status page
        response = requests.get(nginx_status_url)
        response.raise_for_status()
        raw_content = response.text

        # Parse the raw content to extract relevant information
        # You can use regular expressions or custom parsing logic here
        # For example, if the stub_status content is in plain text format, you can split the lines:
        lines = raw_content.strip().split('\n')

        # Pass the parsed information to a template for rendering
        return render_template('status.html', status_info=lines)
    
    except Exception as e:
        # Handle any errors (e.g., network issues, invalid URL, etc.)
        return f"Error: {str(e)}"

@app.route('/logs/<server_name>')
def logs(server_name):
    log_file_path = f'/var/log/nginx/{server_name}.access.log'
    
    # Check if the log file exists
    if not os.path.exists(log_file_path):
        abort(404)  # Return a 404 error if the log file doesn't exist

    # Read the log file and pass its contents to the template
    with open(log_file_path, 'r') as log_file:
        log_contents = log_file.read()

    return render_template('logs.html', log_contents=log_contents)

if __name__ == '__main__':
    app.run(debug=True)

