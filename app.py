from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, timedelta
from flask_celery import Celery
import boto3

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Setup Celery
celery = Celery(app)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=False)
    aws_access_key = db.Column(db.String(50), nullable=False)
    aws_secret_key = db.Column(db.String(50), nullable=False)

    # Add a relationship to instances
    instances = db.relationship('Instance', backref='customer', lazy=True)

class Instance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(db.String(50), nullable=False)
    ami_name = db.Column(db.String(120), nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)

# Celery task to delete AMI
@celery.task
def delete_ami_task(ami_id, aws_access_key, aws_secret_key, aws_region):
    try:
        ec2 = boto3.client('ec2', region_name=aws_region,
                           aws_access_key_id=aws_access_key,
                           aws_secret_access_key=aws_secret_key)

        # Deregister the AMI
        ec2.deregister_image(ImageId=ami_id)

        print(f"AMI deleted successfully. AMI ID: {ami_id}")
    except Exception as e:
        print(f"Error deleting AMI: {str(e)}")

@app.route('/')
def index():
    customers = Customer.query.all()
    return render_template('index.html', customers=customers)

@app.route('/customer/<int:customer_id>', methods=['GET'])
def customer_details(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    return render_template('customer_details.html', customer=customer)

@app.route('/new_instance/<int:customer_id>', methods=['GET'])
def new_instance(customer_id):
    # Retrieve customer information based on customer_id
    customer = Customer.query.get_or_404(customer_id)

    # Add your code for adding a new instance here, if needed

    return render_template('new_instance.html', customer=customer)

@app.route('/add_instance/<int:customer_id>', methods=['POST'])
def add_instance(customer_id):
    # Retrieve customer information based on customer_id
    customer = Customer.query.get_or_404(customer_id)

    # Get instance details from the form
    instance_id = request.form['instance_id']

    # Add the instance details to the customer
    customer.instances.append(Instance(instance_id=instance_id))
    db.session.commit()

    # Create AMI for the added instance
    create_ami(customer, instance_id, customer.aws_access_key, customer.aws_secret_key, 'ap-south-1')
    return redirect(url_for('customer_details', customer_id=customer.id))

@app.route('/delete_instance/<int:customer_id>', methods=['POST'])
def delete_instance(customer_id):
    try:
        # Retrieve customer information based on customer_id
        customer = Customer.query.get_or_404(customer_id)

        # Get instance IDs to delete from the form
        instance_ids = request.form.getlist('instanceId')

        # Loop through the selected instances and delete them
        for instance_id in instance_ids:
            instance = Instance.query.filter_by(customer_id=customer_id, instance_id=instance_id).first()
            if instance:
                # Schedule a Celery task to delete the AMI
                delete_ami_task.apply_async((instance.ami_name, customer.aws_access_key, customer.aws_secret_key, 'ap-south-1'))

                # Remove the instance from the database
                db.session.delete(instance)
                db.session.commit()

        return redirect(url_for('customer_details', customer_id=customer.id))
    except Exception as e:
        print(f"Error deleting instance: {str(e)}")
        return jsonify({'error': 'Failed to delete instance'}), 500

@app.route('/delete_customer/<int:customer_id>', methods=['POST'])
def delete_customer(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    db.session.delete(customer)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/add_customer', methods=['POST'])
def add_customer():
    first_name = request.form['first_name']
    last_name = request.form['last_name']
    email = request.form['email']
    aws_access_key = request.form['aws_access_key']
    aws_secret_key = request.form['aws_secret_key']
    aws_region = request.form['aws_region']
    instance_id = request.form['instance_id']

    new_customer = Customer(
        first_name=first_name,
        last_name=last_name,
        email=email,
        aws_access_key=aws_access_key,
        aws_secret_key=aws_secret_key
    )
    db.session.add(new_customer)
    db.session.commit()

    create_ami(new_customer, instance_id, aws_access_key, aws_secret_key, aws_region)

    return redirect(url_for('index'))

@app.route('/new_customer', methods=['GET'])
def new_customer():
    return render_template('new_customer.html')

@app.route('/search', methods=['GET'])
def search():
    search_term = request.args.get('search')
    customers = Customer.query.filter(Customer.email.like(f"%{search_term}%")).all()
    return render_template('search_results.html', customers=customers)

def create_ami(customer, instance_id, aws_access_key, aws_secret_key, aws_region):
    try:
        ec2 = boto3.client('ec2', region_name=aws_region,
                           aws_access_key_id=aws_access_key,
                           aws_secret_access_key=aws_secret_key)

        response = ec2.create_image(InstanceId=instance_id,
                                    Name=f"{customer.first_name}_{customer.last_name}_{instance_id}_AMI",
                                    NoReboot=True)
        ami_id = response['ImageId']

        # Update the AMI name for the corresponding instance
        instance = Instance.query.filter_by(customer_id=customer.id, instance_id=instance_id).first()
        instance.ami_name = ami_id
        db.session.commit()

        print(f"AMI created successfully. AMI ID: {ami_id}")
    except Exception as e:
        print(f"Error creating AMI: {str(e)}")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    app.run(debug=True, port=8000, host='0.0.0.0')

